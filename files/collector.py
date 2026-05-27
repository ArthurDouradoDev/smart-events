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
        """Retorna lista de medições de KPI para inserção no banco."""
        ...

    @abstractmethod
    def collect_vips(self) -> List[dict]:
        """Retorna última leitura de RSRP/RSRQ de cada VIP."""
        ...

    def start(self):
        self._running = True

    def stop(self):
        self._running = False


# ── Fase 1: CSV ──────────────────────────────────────────────────────

class CsvCollector(BaseCollector):
    """
    Monitora uma pasta de imports e processa novos CSVs.

    Formato esperado para KPIs (export iManager MultiLTE Cell):
      Object, Period(minute), Start Time, {BRDC} Traffic Volume UL LTE,
      {BRDC} Traffic Volume DL LTE, {BRDC} DL User Throughput, {BRDC} UL User Throughput

    Formato esperado para Trace (export Signaling Trace):
      No., Source, Time, Mode, Trace Type, Message Type, Message Direction,
      Cell ID, Call ID, Message Content
    """

    KPI_COLUMN_MAP = {
        "utilization_ul": ["{BRDC} Traffic Volume UL LTE", "UL Traffic Volume"],
        "utilization_dl": ["{BRDC} Traffic Volume DL LTE", "DL Traffic Volume"],
        "throughput_dl":  ["{BRDC} DL User Throughput",    "DL User Throughput"],
        "throughput_ul":  ["{BRDC} UL User Throughput",    "UL User Throughput"],
    }

    VIP_MESSAGE_TYPES = {"RRC_MEAS_RPRT", "RRC_CONN_RECFG_CMP", "S1AP_PATH_SWITCH_REQ"}

    def __init__(self, event_config: dict, import_folder: str):
        super().__init__(event_config)
        self.import_folder = Path(import_folder)
        self._processed = set()

    def collect_kpis(self) -> List[dict]:
        kpi_files = list(self.import_folder.glob("kpi_*.csv"))
        measurements = []

        for fpath in kpi_files:
            if fpath.name in self._processed:
                continue
            try:
                measurements.extend(self._parse_kpi_csv(fpath))
                self._processed.add(fpath.name)
                logger.info(f"KPI CSV processado: {fpath.name}")
            except Exception as e:
                logger.error(f"Erro ao processar {fpath.name}: {e}")

        return measurements

    def collect_vips(self) -> List[dict]:
        trace_files = list(self.import_folder.glob("trace_*.csv"))
        measurements = []

        for fpath in trace_files:
            if fpath.name in self._processed:
                continue
            try:
                measurements.extend(self._parse_trace_csv(fpath))
                self._processed.add(fpath.name)
                logger.info(f"Trace CSV processado: {fpath.name}")
            except Exception as e:
                logger.error(f"Erro ao processar {fpath.name}: {e}")

        return measurements

    def _parse_kpi_csv(self, fpath: Path) -> List[dict]:
        rows = []
        with open(fpath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                site_id = self._extract_site_id(row.get("Object", ""))
                if site_id not in self.site_ids:
                    continue

                timestamp = row.get("Start Time", datetime.utcnow().isoformat())
                cell_id = row.get("Object", site_id)

                for metric, column_candidates in self.KPI_COLUMN_MAP.items():
                    value = None
                    for col in column_candidates:
                        if col in row and row[col]:
                            try:
                                value = float(row[col])
                            except ValueError:
                                pass
                            break
                    if value is not None:
                        rows.append({
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "event_id":  self.event_id,
                            "timestamp": timestamp,
                            "metric":    metric,
                            "value":     value,
                        })
        return rows

    def _parse_trace_csv(self, fpath: Path) -> List[dict]:
        """Extrai RSRP/RSRQ dos eventos RRC_MEAS_RPRT de VIPs."""
        rows = []
        with open(fpath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                msg_type = row.get("Message Type", "").strip()
                if msg_type not in self.VIP_MESSAGE_TYPES:
                    continue

                source = row.get("Source", "")
                vip_name = self._resolve_vip(source)
                if not vip_name:
                    continue

                cell_id = row.get("Cell ID", "")
                in_event = cell_id in self.site_ids

                rsrp, rsrq = self._extract_rsrp_rsrq(row.get("Message Content", ""))
                if rsrp is None:
                    continue

                rows.append({
                    "vip_name":    vip_name,
                    "event_id":    self.event_id,
                    "timestamp":   row.get("Time", datetime.utcnow().isoformat()),
                    "serving_cell": cell_id,
                    "rsrp":        rsrp,
                    "rsrq":        rsrq,
                    "in_event":    1 if in_event else 0,
                })
        return rows

    def _extract_site_id(self, obj_string: str) -> str:
        # "SR-SPCBA1-eNodeB Fun..." → usa prefix como site ID
        # Ajuste conforme o formato real do seu iManager
        return obj_string.split("-")[0] if obj_string else obj_string

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
            for line in content.split("|"):
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

class HttpCollector(BaseCollector):
    """
    FASE 2: Coleta via requisições HTTP ao iManager.

    Para descobrir os endpoints:
    1. Abre o iManager no Chrome
    2. DevTools → Network → filtra por XHR/Fetch
    3. Executa a atualização da tabela
    4. Inspeciona URL, headers e response
    5. Preenche os métodos abaixo

    Autenticação: geralmente cookie de sessão JSESSIONID.
    """

    def __init__(self, event_config: dict, base_url: str, session_cookie: str = ""):
        super().__init__(event_config)
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie
        self._session = None

    def _get_session(self):
        import requests
        if not self._session:
            self._session = requests.Session()
            if self.session_cookie:
                self._session.cookies.set("JSESSIONID", self.session_cookie)
        return self._session

    def collect_kpis(self) -> List[dict]:
        # TODO: implementar quando endpoints forem descobertos via DevTools
        # Estrutura esperada:
        # session = self._get_session()
        # resp = session.get(f"{self.base_url}/oss/monitoring/kpi", params={...})
        # return self._parse_response(resp.json())
        raise NotImplementedError("HttpCollector.collect_kpis não implementado ainda")

    def collect_vips(self) -> List[dict]:
        # TODO: implementar para trace de VIPs
        raise NotImplementedError("HttpCollector.collect_vips não implementado ainda")


# ── Mock (desenvolvimento e testes) ──────────────────────────────────

class MockCollector(BaseCollector):
    """Gera dados sintéticos para desenvolvimento sem acesso ao OSS."""

    import random as _random

    def collect_kpis(self) -> List[dict]:
        import random
        rows = []
        now = datetime.utcnow().isoformat()
        sites = self.event.get("sites", [])

        for site in sites:
            for i, cell in enumerate(site.get("cells", [{"id": "Cell-A1"}])):
                cell_id = cell if isinstance(cell, str) else cell.get("id", f"Cell-{i}")
                for metric, base in [
                    ("utilization", 50), ("availability", 98),
                    ("throughput_dl", 40), ("throughput_ul", 15)
                ]:
                    value = max(0, base + random.gauss(0, 10))
                    rows.append({
                        "site_id":   site["id"],
                        "cell_id":   cell_id,
                        "event_id":  self.event_id,
                        "timestamp": now,
                        "metric":    metric,
                        "value":     round(value, 2),
                    })
        return rows

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
