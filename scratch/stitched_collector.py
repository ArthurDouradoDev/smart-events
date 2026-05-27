"""
Collector: responsável por buscar dados do OSS e Trace.

Fase 1 (atual): lê CSVs exportados manualmente da interface iManager.
               Monitora uma pasta configurada no evento e processa
               novos arquivos automaticamente.

Fase 2 (planejada): HTTP direto via sessão autenticada no iManager.
                    Descobre endpoints via interceptação no DevTools.

Para dev/testes: MockCollector gera dados sintéticos.
"""

import csv
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core import database as db

logger = logging.getLogger(__name__)


# ── Interface base ───────────────────────────────────────────────────

class BaseCollector(ABC):
    def __init__(self, event_config: dict):
        self.event = event_config
        self.event_id = event_config["id"]
        self.site_ids = {s["id"] for s in event_config.get("sites", [])}
        self.vip_imsis = {v["imsi"]: v["name"] for v in event_config.get("vips", [])}
        self.thresholds = event_config.get("thresholds", {})
        self._running = False

    @abstractmethod
    def collect_kpis(self) -> List[dict]:
        """Retorna lista de medições de KPI para in
        for site_id in self.site_ids:
            if site_id in obj_string:
                return site_id

        # Fallback padrão
        return obj_string.split("-")[0]

    def _resolve_vip(self, source: str) -> Optional[str]:
        for imsi, name in self.vip_imsis.items():
            if imsi in source:
                return name
        return None

    def _extract_rsrp_rsrq(self, content: str) -> tuple:
        """
        Extrai RSRP/RSRQ do conteúdo da mensagem.
        Formato iManager: "rsrpResult: ---- 0x30(48)"
        Mapeamento LTE: RSRP = index - 140 dBm
        """
        rsrp = rsrq = None
        try:
            lines = re.split(r'[|\n]', content) if content else []
            for line in lines:
                line = line.strip()
                if "rsrpResult" in line and "0x" in line:
                    index = int(line.split("(")[1].split(")")[0])
                    rsrp = index - 140.0
                elif "rsrqResult" in line and "0x" in line:
                    index = int(line.split("(")[1].split(")")[0])
                    rsrq = (index / 2.0) - 19.5  # mapeamento LTE RSRQ
        except Exception:
            pass
        return rsrp, rsrq


# ── Fase 2: HTTP (skeleton) ───────────────────────────────────────────

class SessionExpiredError(Exception):
    """Exceção levantada quando a sessão do iManager expira."""
    pass


class HttpCollector(BaseCollector):
    """
    FASE 2: Coleta via requisições HTTP ao iManager com renovação de sessão automática.
    """

    KPI_COLUMN_MAP = {
        "utilization_ul": ["{BRDC} Traffic Volume UL LTE", "UL Traffic Volume", "Traffic Volume UL LTE"],
        "utilization_dl": ["{BRDC} Traffic Volume DL LTE", "DL Traffic Volume", "Traffic Volume DL LTE"],
        "throughput_dl":  ["{BRDC} DL User Throughput",    "DL User Throughput"],
        "throughput_ul":  ["{BRDC} UL User Throughput",    "UL User Throughput"],
    }

    VIP_MESSAGE_TYPES = {"RRC_MEAS_RPRT", "RRC_CONN_RECFG_CMP", "S1AP_PATH_SWITCH_REQ"}

    def __init__(self, event_config: dict, base_url: str, session_cookie: str = ""):
        super().__init__(event_config)
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie
        self._session_monitoring = None
        self._session_trace = None
        self._renew_lock = threading.Lock()
        
        # Build obj_no-to-cell-info mapping for KPIs
        self._obj_to_cell = {}
        self._cell_to_site = {}
        for site in event_config.get("sites", []):
            for cell in site.get("cells", []):
                c_id = cell if isinstance(cell, str) else cell.get("id")
                if c_id:
                    self._cell_to_site[c_id] = site["id"]
                    if isinstance(cell, dict) and "obj_no" in cell:
                        self._obj_to_cell[int(cell["obj_no"])] = {
                            "cell_id": c_id,
                            "site_id": s
                
                exec_time = res_item.get("execTime") or task_data.get("execTime")
                if exec_time:
                    timestamp = datetime.utcfromtimestamp(exec_time / 1000.0).isoformat()
                else:
                    timestamp = datetime.utcnow().isoformat()
                
                for metric, candidates in self.KPI_COLUMN_MAP.items():
                    val = self._find_metric_value(res_item, candidates)
                    if val is not None:
                        try:
                            rows.append({
                                "site_id":   cell_info["site_id"],
                                "cell_id":   cell_info["cell_id"],
                                "event_id":  self.event_id,
                                "timestamp": timestamp,
                                "metric":    metric,
                                "value":     float(val),
                            })
                        except (ValueError, TypeError):
                            pass
        return rows

    def _find_metric_value(self, d, candidates):
        if not isinstance(d, dict):
            return None
        for k, v in d.items():
            if k in candidates:
                return v
        for k, v in d.items():
            if isinstance(v, dict):
                val = self._find_metric_value(v, candidates)
                if val is not None:
                    return val
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        val = self._find_metric_value(item, candidates)
                        if val is not None:
                            return val
        return None

    def collect_vips(self) -> List[dict]:
        integration = self.event.get("integration", {})
        sess_data = self._load_session_data()
        trace_task_ids = sess_data.get("trace", {}).get("task_id")
        if trace_task_ids:
            trace_task_ids = [trace_task_ids]
        else:
            trace_task_ids = integration.get("trace_task_ids", [1925])

        if not trace_task_ids:
            logger.warning("Nenhum trace_task_ids configurado para busca de VIPs.")
            return []

        now_ms = int(time.time() * 1000)
        measurements = []

        for task_id in trace_task_ids:
            trace_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/sort?nocache={now_ms}"
            trace_payload = {
                "msgId": -1,
                "comparisonMsgId": -1,
                "taskId": task_id,
                "isAscend": True,
                "sqlColumnName": "Time",
                "startRow": 0,
                "pageSize": 1000,
                "templateName": [],
                "isSetBenchMarkTime": False,
                "benchMarkTimeRowNo": -1
            }

            retries = 2
            for attempt in range(retries):
                s = self._get_session("trace")
                used_cookie = s.c
        return []

    def collect_vips(self) -> List[dict]:
        import random
        rows = []
        now = datetime.utcnow().isoformat()
        sites = self.event.get("sites", [])
        site_ids = [s["id"] for s in sites]

        for vip in self.event.get("vips", []):
            serving = random.choice(site_ids) if site_ids else ""
            in_event = serving in self.site_ids
            rsrp = round(random.gauss(-88, 8), 1)
            rsrq = round(random.gauss(-9, 2), 1)
            rows.append({
                "vip_name":    vip["name"],
                "event_id":    self.event_id,
                "timestamp":   now,
                "serving_cell": serving,
                "rsrp":        rsrp,
                "rsrq":        rsrq,
                "in_event":    1 if in_event else 0,
            })
        return rows


# ── Factory ──────────────────────────────────────────────────────────

def build_collector(event_config: dict, mock: bool = False) -> BaseCollector:
    if mock:
        return MockCollector(event_config)

    oss = event_config.get("oss", {})
    import_folder = oss.get("import_folder", "")

    if import_folder and Path(import_folder).exists():
        return CsvCollector(event_config, import_folder)

    base_url = oss.get("base_url", "")
    if base_url:
        return HttpCollector(event_config, base_url)

    logger.warning("Nenhuma fonte de dados configurada — usando MockCollector")
    return MockCollector(event_config)
