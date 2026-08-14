"""
Collector: responsável por buscar dados do OSS e Trace.

Fase 1 (atual): lê CSVs exportados manualmente da interface iManager.
               Monitora uma pasta configurada no evento e processa
               novos arquivos automaticamente.

Fase 2 (implementada): HTTP direto via sessão autenticada no iManager.
                    Renova sessão automaticamente via Playwright quando expirar.

Para dev/testes: MockCollector gera dados sintéticos (use --mock na linha de comando).
"""

import csv
import json
import logging
import os
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

# Import requests safely without namespace package shadowing from the local workspace folder.
# No executável compilado (PyInstaller) NÃO mexer no sys.path: ali Path(__file__).parent.parent
# é o diretório de extração (_MEIPASS) e removê-lo quebraria a resolução do requests empacotado.
import sys
if getattr(sys, "frozen", False):
    import requests
else:
    _orig_path = list(sys.path)
    try:
        _cwd = Path.cwd().resolve()
        _parent = Path(__file__).parent.parent.resolve()
        sys.path = [p for p in sys.path if p and Path(p).resolve() not in (_cwd, _parent)]
        import requests
    finally:
        sys.path = _orig_path

import urllib3

from core import database as db
from core import credentials
from core.collection_result import CollectionResult
from core.kpi_formulas import InvalidKpi, calculate as calculate_kpi, definitions_for
from core.rrc_decode import decode_meas_report
from core.session_renew import (
    EXIT_SUCCESS,
    EXIT_GENERIC_FAIL,
    EXIT_NEEDS_INTERACTIVE,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Mapeamento de regional para base_url do iManager.
# oss.base_url no evento sempre tem precedência sobre este mapa.
_REGIONAL_BASE_URLS: dict[str, str] = {
    "SP": "https://10.220.50.9:31943",
    "RJ": "https://10.220.30.9:31943",
}
_DEFAULT_BASE_URL = "https://10.220.50.9:31943"  # fallback = SP

# Quantos corpos de Monitoring sem medição são gravados automaticamente por processo.
MONITORING_AUTO_DUMPS = 3


# ── Coleta de alarmes (iMaster FM website) ───────────────────────────
# Lógica portada de imaster_alarms.py (validada AO VIVO na VPN em 30/06): o cmd 1102
# cria a "visão" filtrada (modelID) a partir dos pares {alarmId, alarmGroupId}; o cmd
# 1103 pagina o resultado já filtrado. Mantemos o standalone intacto (sem import cruzado)
# como fonte de referência; aqui reusamos a sessão 'monitoring' do session.json — o mesmo
# host/sessão do OSS/PM serve ao endpoint /rest/fmwebsite (bspsession + roarand).
_ALARM_ENDPOINT = "/rest/fmwebsite/v1/commands"
_ALARM_PAGE = 148            # tamanho da janela (igual ao navegador)
_ALARM_SLEEP = 0.3           # pausa entre páginas, para não martelar o servidor
_DEFAULT_ALARM_NAMES = ["RF Unit VSWR Threshold Crossed", "Cell Unavailable"]

_ALARM_SEVERITY = {1: "Critical", 2: "Major", 3: "Minor", 4: "Warning",
                   5: "Indeterminate", 6: "Cleared"}

# condition fixa: níveis/status/eventType que a tela "Current Alarms" sempre envia.
_ALARM_BASE_CONDITION = {
    "alarmLevel": ["CRITICAL", "MAJOR", "MINOR", "WARNING"],
    "alarmStatus": [12, 10, 11, 13],
    "eventType": {"value": [str(i) for i in range(1, 17)], "operation": "in"},
    "specialAlarmStatus": {"value": ["0"], "operation": "in"},
    "orders": [{"field": "ColArriveUtc", "order": 1}],
}
# additionalCondition fixa (igual nos HAR com e sem filtro).
_ALARM_ADDITIONAL_CONDITION = {
    "alarmGroupId": {"operation": "in", "value": []},
    "soundInfoCond": [
        {"severity": s, "alarmStatus": "1", "duration": 60} for s in (1, 2, 3, 4)
    ],
}

# Catálogo (nome → pares {alarmId, alarmGroupId}) carregado 1× e cacheado em memória.
_alarm_catalog_cache: Optional[dict] = None


def _alarm_catalog_path() -> Path:
    """Caminho do catálogo empacotado (recurso read-only).

    frozen (.exe onedir) → sys._MEIPASS/alarms; dev → <repo>/alarms. NÃO usa a pasta
    persistente data/ (o catálogo é recurso empacotado, não gravável)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).parent.parent
    return base / "alarms" / "catalogo-alarmes.csv"


def _load_alarm_catalog() -> dict:
    """Lê o catálogo {nome: [(alarmId, alarmGroupId), ...]} (cacheado em memória).

    O CSV tem BOM e cabeçalho (Alarm Group ID, Alarm Group Name, Alarm ID, Alarm Name,
    Alarm Severity). Um mesmo nome aparece em vários grupos (e até com alarmId diferente
    por grupo), então acumulamos todos os pares por nome. Alguns nomes vêm com tabs/
    espaços nas pontas, então normalizamos com strip()."""
    global _alarm_catalog_cache
    if _alarm_catalog_cache is not None:
        return _alarm_catalog_cache
    catalog: dict = {}
    with open(_alarm_catalog_path(), encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Alarm Name") or "").strip()
            alarm_id = (row.get("Alarm ID") or "").strip()
            group_id = (row.get("Alarm Group ID") or "").strip()
            if not (name and alarm_id and group_id):
                continue
            pair = (alarm_id, group_id)
            pairs = catalog.setdefault(name, [])
            if pair not in pairs:
                pairs.append(pair)
    _alarm_catalog_cache = catalog
    return catalog


def _resolve_alarm_pairs(names: List[str], catalog: dict):
    """Traduz nomes → pares (alarmId, alarmGroupId), deduplicando entre nomes.

    Diferente do standalone (que levanta em nome inexistente), aqui somos tolerantes:
    o filtro vem da config do evento e pode ter um tipo obsoleto — ignoramos os nomes
    ausentes e devolvemos (pares, faltando) para o chamador logar."""
    pairs: list = []
    missing: list = []
    for name in names:
        key = (name or "").strip()
        if not key:
            continue
        if key in catalog:
            for pair in catalog[key]:
                if pair not in pairs:
                    pairs.append(pair)
        else:
            missing.append(key)
    return pairs, missing


def _build_alarm_condition(pairs: list) -> str:
    """Monta a condition (string JSON) do cmd 1102 com os pares filtrados."""
    condition = dict(_ALARM_BASE_CONDITION)
    if pairs:
        value = [{"alarmId": aid, "alarmGroupId": gid} for aid, gid in pairs]
        condition["alarmGroupId"] = {"operation": "in", "value": value}
    return json.dumps(condition)


def _extract_alarm_model_id(payload: dict):
    """Procura o modelID novo na resposta do 1102 em vários caminhos possíveis."""
    if not isinstance(payload, dict):
        return None
    params = payload.get("parameters", {})
    for container in (params, payload):
        if not isinstance(container, dict):
            continue
        mid = container.get("modelID") or container.get("modelId")
        if mid:
            return mid
        result = container.get("result")
        if isinstance(result, dict):
            mid = result.get("modelID") or result.get("modelId")
            if mid:
                return mid
    return None


def _flatten_alarm(a: dict, event_id: str, collected_at: str) -> dict:
    """Achata a linha crua do 1103 nos campos gravados na tabela `alarms`."""
    ext = a.get("extParams") or {}
    return {
        "csn":             a.get("csn"),
        "event_id":        event_id,
        "alarm_id":        a.get("alarmId"),
        "alarm_group_id":  a.get("alarmGroupId"),
        "alarm_name":      a.get("alarmName"),
        "severity":        _ALARM_SEVERITY.get(a.get("severity"), a.get("severity")),
        "source":          a.get("meName") or ext.get("alarmSource"),
        "ip":              a.get("address"),
        "location":        a.get("subNet"),
        "occur_time":      a.get("occurUtc") or a.get("firstOccurUtc"),
        "arrive_time":     a.get("arriveUtc"),
        "additional_info": a.get("additionalInformation"),
        "collected_at":    collected_at,
    }


# ── Interface base ───────────────────────────────────────────────────

class BaseCollector(ABC):
    def __init__(self, event_config: dict):
        self.event = event_config
        self.event_id = event_config["id"]
        self.site_ids = {s["id"] for s in event_config.get("sites", [])}
        self.cell_ids = set()
        self._cell_to_site_index = {}
        for site in event_config.get("sites", []):
            for cell in site.get("cells", []):
                c_id = cell if isinstance(cell, str) else cell.get("id")
                if c_id:
                    self.cell_ids.add(c_id)
                    self._cell_to_site_index[c_id] = site["id"]
        # vips_by_task mapeia task_id -> nome do VIP, obtido por OSS do evento
        self.vips_by_task = {}
        try:
            event_vips = db.get_event_vips(self.event_id)
            for v in event_vips:
                task_id = v.get("task_id")
                name = v.get("name")
                if task_id is not None and name:
                    self.vips_by_task[task_id] = name
        except Exception as e:
            logger.error(f"Erro ao inicializar VIPs do evento {self.event_id}: {e}")
        self.thresholds = event_config.get("thresholds", {})
        self._running = False

    def _load_vips_by_task(self) -> dict:
        """Lê task_ids dos VIPs do banco filtrados pelo OSS do evento atual."""
        result = {}
        try:
            event_vips = db.get_event_vips(self.event_id)
            for v in event_vips:
                task_id = v.get("task_id")
                name = v.get("name")
                if task_id is not None and name:
                    result[task_id] = name
        except Exception as e:
            logger.error(f"Erro ao carregar VIPs do evento {self.event_id}: {e}")
        return result

    def _cell_in_event(self, cell_id: str) -> bool:
        """Determina se um cell_id pertence a algum site do evento.
        Aceita match exato, mapeamento de obj_no, substring ou decodificação de ECI/NCI."""
        if not cell_id:
            return False
        cell_id_str = str(cell_id).strip()
        cell_id_upper = cell_id_str.upper()
        
        # 1. Match exato com as células do evento
        cell_ids_upper = {c.upper() for c in self.cell_ids}
        if cell_id_upper in cell_ids_upper:
            return True
            
        # 2. Match por obj_no mapeado nos sites
        for site in self.event.get("sites", []):
            for cell in site.get("cells", []):
                if isinstance(cell, dict):
                    obj_no = cell.get("obj_no")
                    if obj_no is not None and str(obj_no) == cell_id_str:
                        return True

        # 3. Match por prefixo ou contendo o site_id ou nome do site
        site_ids_upper = {s.upper() for s in self.site_ids}
        for s in site_ids_upper:
            if cell_id_upper.startswith(s) or s in cell_id_upper:
                return True
        for site in self.event.get("sites", []):
            s_name = site.get("name", "")
            if s_name:
                s_name_upper = s_name.upper()
                if s_name_upper in cell_id_upper or cell_id_upper in s_name_upper:
                    return True
                
        # 4. Decodificação de ID global de célula (4G ECI // 256 ou 5G NCI // 4096)
        if cell_id_str.isdigit():
            try:
                val = int(cell_id_str)
                for divisor in (256, 4096):
                    inferred_site = val // divisor
                    inferred_str = str(inferred_site)
                    if inferred_site > 0:
                        for s in site_ids_upper:
                            if inferred_str in s:
                                return True
            except ValueError:
                pass
                
        return False

    @staticmethod
    def _normalize_cell_technology(value, cell_id: str = "") -> Optional[str]:
        text = f"{value or ''} {cell_id or ''}".upper()
        has_4g = bool(re.search(r"(^|[^A-Z0-9])(?:4G|LTE)([^A-Z0-9]|$)", text))
        has_5g = bool(re.search(r"(^|[^A-Z0-9])(?:5G|NR|NCI)([^A-Z0-9]|$)", text))
        if has_4g and has_5g:
            return None
        if has_4g:
            return "4G"
        if has_5g:
            return "5G"
        return None

    @staticmethod
    def _normalized_name(value) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    def _configured_pm_tasks(self, session_data: dict) -> list[dict]:
        """Resolve tasks por tecnologia, sem trocar silenciosamente 4G por 5G."""
        integration = self.event.get("integration", {}) or {}
        configured = integration.get("pm_tasks") or []
        if isinstance(configured, dict):
            configured = [{"tech": tech, "task_id": task} for tech, task in configured.items()]
        tasks = []
        for item in configured:
            if not isinstance(item, dict) or item.get("task_id") is None:
                continue
            tech = self._normalize_cell_technology(item.get("tech"))
            if tech:
                tasks.append({"task_id": int(item["task_id"]), "technology": tech})
        if not tasks:
            fallback = integration.get("pm_task_id") or session_data.get("monitoring", {}).get("task_id")
            if fallback is not None:
                tasks.append({"task_id": int(fallback), "technology": "4G"})
        # Uma task por tecnologia; duplicidade de configuração é ambígua e deve falhar visivelmente.
        seen = set()
        unique = []
        for item in tasks:
            if item["technology"] in seen:
                raise ValueError(f"Mais de uma task PM configurada para {item['technology']}")
            seen.add(item["technology"])
            unique.append(item)
        return unique

    def _request_objects_for_task(self, task: dict) -> list[int]:
        tech = task["technology"]
        known = [obj for obj, info in self._obj_to_cell.items()
                 if info.get("technology") == tech or (tech == "4G" and info.get("technology") is None)]
        # Descoberta aberta ocorre no máximo uma vez por task nesta instância. Depois,
        # células ainda não mapeadas viram cobertura parcial, não uma consulta crescente.
        if not known and str(task["task_id"]) not in self._discovered_pm_tasks:
            return []
        return known

    def _monitoring_payload(self, tasks: list[dict], oss: str) -> list[dict]:
        """Monta a consulta sem deixar um checkpoint antigo impedir descoberta.

        Enquanto nenhum ``objNo`` da task foi associado a uma célula, a busca é
        aberta desde zero. Um cursor geral gravado por versões anteriores não é
        evidência de cobertura e não pode esconder novamente os nomes do OSS.
        """
        payload = []
        for task in tasks:
            checkpoints = db.get_collection_checkpoints(
                self.event_id, "monitoring", task["task_id"], oss)
            objects = self._request_objects_for_task(task)
            fallback = checkpoints.get("")
            discovering = not objects
            item = {
                "taskId": task["task_id"],
                "preExecTime": 0 if discovering else (
                    int(fallback) if fallback and str(fallback).isdigit() else 0),
            }
            # Na descoberta o navegador envia apenas ``[{"taskId": N, "preExecTime": 0}]``;
            # a chave ``objNoExecTimes`` só aparece quando há objetos. Enviar uma lista
            # vazia não é o mesmo contrato e não pode ser assumido equivalente.
            if not discovering:
                item["objNoExecTimes"] = [
                    {
                        "objNo": obj_no,
                        "preExecTime": int(checkpoints.get(str(obj_no), fallback or 0)),
                    }
                    for obj_no in objects
                ]
            payload.append(item)
        return payload

    def _log_unmapped(self, parsed: dict) -> None:
        """Torna visível a única falha de coleta que era silenciosa: o OSS responde, o
        cursor avança e todas as linhas são descartadas porque o nome das células do
        evento não é o nome que aquele OSS usa. Loga os dois lados da comparação — sem
        isso o operador só vê o ciclo como 'parcial', sem causa."""
        if not parsed["unmapped"]:
            return
        esperados = sorted(self.cell_ids)[:3]
        logger.warning(
            f"[monitoring] {parsed['unmapped']} de {parsed['received']} objetos não casaram "
            f"com nenhuma célula do evento e foram DESCARTADOS. "
            f"Nomes vindos do OSS (até 10): {parsed['unmapped_cells'][:10]} | "
            f"exemplos de células cadastradas no evento: {esperados}"
        )

    def _log_monitoring_cycle(self, response, payload: list[dict], discovery_tasks: set,
                              parsed: dict, response_payload) -> None:
        """Registra o resultado de cada ciclo de PM, como já era feito em VIP e alarmes.

        Sem esta linha, um ciclo que responde 200 sem objetos é indistinguível de um
        worker parado: ambos não escrevem nada no log, no banco e nos checkpoints.
        Quando o ciclo não produz medição, o corpo é gravado em ``data/diagnostics``
        (limitado por processo) para que a causa possa ser lida sem novo build.
        """
        tasks_desc = ", ".join(
            f"{item['taskId']}"
            f"{'/descoberta' if str(item['taskId']) in discovery_tasks else '/' + str(len(item.get('objNoExecTimes') or [])) + 'obj'}"
            for item in payload
        )
        logger.info(
            "[monitoring] tasks=%s HTTP=%s recebidos=%s mapeados=%s nao_mapeados=%s "
            "invalidos=%s linhas=%s cursores=%s",
            tasks_desc or "-", response.status_code, parsed["received"],
            parsed["received"] - parsed["unmapped"], parsed["unmapped"],
            parsed["invalid"], len(parsed["rows"]), len(parsed["cursors"]),
        )
        if parsed["rows"] or self._auto_dumps_monitoring >= MONITORING_AUTO_DUMPS:
            return
        if self._dump_raw(response_payload, "monitoring", force=True):
            self._auto_dumps_monitoring += 1

    def _collect_kpis_v2(self) -> CollectionResult:
        session_data = self._load_session_data()
        tasks = self._configured_pm_tasks(session_data)
        if not tasks:
            logger.warning("[monitoring] nenhuma task PM configurada para as tecnologias do evento.")
            return CollectionResult.partial(cause="Nenhuma task PM foi configurada para as tecnologias do evento.",
                                            coverage={"cells_mapped": 0, "cells_expected": len(self.cell_ids)})
        oss = (self._region or self.base_url).upper()
        payload = self._monitoring_payload(tasks, oss)
        discovery_tasks = {
            str(item["taskId"]) for item in payload if not item.get("objNoExecTimes")
        }
        url = f"{self.base_url}/rest/oss/access/pm/v1/monitor/task/result"
        renewed = False
        for attempt in range(2):
            try:
                response = self._get_session("monitoring").post(
                    url, json=payload, timeout=30, headers={"x-non-renewal-session": "true"}, allow_redirects=False)
                if not self._check_session_valid(response, "monitoring"):
                    raise SessionExpiredError("Sessão Monitoring expirada")
                response.raise_for_status()
                response_payload = response.json()
                self._dump_raw(response_payload, "monitoring")
                parsed = self._parse_monitoring_response(
                    response_payload, {str(t["task_id"]): t["technology"] for t in tasks}
                )
                # Uma resposta vazia só confirma o cursor geral depois que a
                # cobertura da task já foi estabelecida. Durante descoberta,
                # mantemos a consulta em zero até vermos e resolvermos objetos.
                if parsed["received"] == 0:
                    for task_id in discovery_tasks:
                        parsed["cursors"].pop(f"{task_id}:", None)
                # Depois do descarte de descoberta: o número de cursores logado é o
                # que será realmente confirmado no banco.
                self._log_monitoring_cycle(response, payload, discovery_tasks, parsed,
                                           response_payload)
                for task in tasks:
                    self._discovered_pm_tasks.add(str(task["task_id"]))
                coverage = {
                    "cells_mapped": len(self._obj_to_cell), "cells_expected": len(self.cell_ids),
                    "mapped_objects": parsed["received"] - parsed["unmapped"],
                    "unmapped_objects": parsed["unmapped"], "unmapped_cells": parsed["unmapped_cells"],
                    "event_cells": sorted(self.cell_ids)[:5],
                }
                self._log_unmapped(parsed)
                partial = bool(parsed["unmapped"] or parsed["invalid"] or len(self._obj_to_cell) < len(self.cell_ids))
                kwargs = dict(cursors=parsed["cursors"], received=parsed["received"],
                              calculated=len(parsed["rows"]), invalid=parsed["invalid"],
                              diagnostics=parsed["diagnostics"], coverage=coverage,
                              latest_data_at=parsed["latest_data_at"])
                if partial:
                    return CollectionResult.partial(parsed["rows"], cause="Cobertura ou fórmulas de Monitoring parciais.", **kwargs)
                if parsed["rows"]:
                    return CollectionResult.data(parsed["rows"], **kwargs)
                return CollectionResult.empty("Monitoring respondeu sem medições novas.", **kwargs)
            except SessionExpiredError:
                if renewed or attempt:
                    logger.error("Sessão de Monitoring continuou inválida após a renovação (KPIs v2).")
                    return CollectionResult.auth_required("A sessão de Monitoring continuou inválida após a renovação.")
                if not self._renew_session("monitoring"):
                    logger.error("Não foi possível renovar a sessão de Monitoring (KPIs v2).")
                    return CollectionResult.auth_required("Não foi possível renovar a sessão de Monitoring.")
                renewed = True
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as error:
                logger.error(f"Erro de conexão ao consultar KPIs: {error}")
                return CollectionResult.error(f"Falha de conexão ao consultar KPIs: {error}", code="network")
            except requests.exceptions.HTTPError as error:
                logger.error(f"HTTP ao consultar KPIs: {error}")
                return CollectionResult.error(f"Falha HTTP ao consultar KPIs: {error}", code="http")
            except (ValueError, TypeError, KeyError) as error:
                logger.error(f"Resposta inválida do Monitoring: {error}")
                return CollectionResult.error(f"Resposta inválida do Monitoring: {error}", stage="parsing", code="contract")
        return CollectionResult.error("Monitoring terminou sem resposta.", code="unknown")

    def collect_kpis(self) -> CollectionResult:
        return self._collect_kpis_v2()

    def _legacy_collect_kpis(self) -> CollectionResult:
        """Coleta KPIs e descreve explicitamente o resultado do ciclo."""
        ...

    @abstractmethod
    def collect_vips(self, mode: str = "express") -> CollectionResult:
        """Coleta traces de VIP e descreve explicitamente o resultado do ciclo."""
        ...

    @abstractmethod
    def collect_alarms(self) -> CollectionResult:
        """Coleta alarmes e descreve explicitamente o resultado do ciclo."""
        ...

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

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


# ── Fase 1: CSV ──────────────────────────────────────────────────────

class CsvCollector(BaseCollector):
    """
    Monitora uma pasta de imports e processa novos CSVs.

    Formato esperado para KPIs (export iManager MultiLTE Cell):
      Object, Period(minute), Start Time, {BRDC} Traffic Volume UL LTE,
      {BRDC} Traffic Volume DL LTE, {BRDC} DL User Throughput, {BRDC} UL User Throughput
    """

    KPI_COLUMN_MAP = {
        "utilization_dl":    ["DL PRB USAGE", "DL PRB Usage"],
        "traffic_volume_dl": ["Traffic Volume DL", "{BRDC} Traffic Volume DL LTE", "Traffic Volume DL LTE", "{BRDC} NR DL Traffic Volume", "NR DL Traffic Volume"],
        "traffic_volume_ul": ["Traffic Volume UL", "{BRDC} Traffic Volume UL LTE", "Traffic Volume UL LTE", "{BRDC} NR UL Traffic Volume", "NR UL Traffic Volume"],
        "throughput_dl":     ["DL User Throughput", "{BRDC} DL User Throughput", "{BRDC} DL User Throughput LTE", "DL User Throughput LTE", "{BRDC} NR DL User Throughput", "NR DL User Throughput"],
        "throughput_ul":     ["UL User Throughput", "{BRDC} UL User Throughput", "{BRDC} UL User Throughput LTE", "UL User Throughput LTE", "{BRDC} NR UL User Throughput", "NR UL User Throughput"],
        "user_count":        ["{BRDC} Usuario", "Usuario", "Active Users", "{BRDC} User PCell"],
        "accessibility":     ["{BRDC} Acessibilidade", "Acessibilidade RRC", "ACC RRC", "Accessibility"],
    }

    def __init__(self, event_config: dict, import_folder: str):
        super().__init__(event_config)
        self.import_folder = Path(import_folder)
        self._processed = set()

    def collect_kpis(self) -> CollectionResult:
        kpi_files = list(self.import_folder.glob("kpi_*.csv"))
        measurements = []
        diagnostics = []

        for fpath in kpi_files:
            if fpath.name in self._processed:
                continue
            try:
                measurements.extend(self._parse_kpi_csv(fpath))
                self._processed.add(fpath.name)
                logger.info(f"KPI CSV processado: {fpath.name}")
            except Exception as e:
                logger.error(f"Erro ao processar {fpath.name}: {e}")
                diagnostics.append((fpath.name, str(e)))

        if diagnostics:
            return CollectionResult.partial(
                measurements, cause="Um ou mais arquivos CSV não puderam ser processados.",
                coverage={"files_failed": len(diagnostics), "files_processed": len(self._processed)},
            )
        if measurements:
            return CollectionResult.data(measurements, coverage={"files_processed": len(self._processed)})
        return CollectionResult.empty("Nenhum CSV novo de KPI encontrado.")

    def collect_vips(self, mode: str = "express") -> CollectionResult:
        # CSV de trace de VIPs foi descontinuado: o esquema atual identifica
        # cada VIP pelo task_id da sua task dedicada no iManager (via HTTP).
        # Se você ainda precisa importar trace de CSV, ele teria que carregar
        # uma coluna explícita com o nome do VIP.
        return CollectionResult.empty("Importação de trace por CSV não está configurada.")

    def collect_alarms(self) -> CollectionResult:
        # Coleta de alarmes por CSV está fora de escopo (só via HTTP no iManager).
        return CollectionResult.empty("Importação de alarmes por CSV não está configurada.")

    def _parse_kpi_csv(self, fpath: Path) -> List[dict]:
        rows = []
        with open(fpath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                site_id = self._extract_site_id(row.get("Object", ""))
                if site_id not in self.site_ids:
                    continue

                raw_ts = row.get("Start Time")
                if raw_ts:
                    timestamp = raw_ts.strip().replace(" ", "T")
                    if not timestamp.endswith("Z"):
                        timestamp += "Z"
                else:
                    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

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

    def _extract_site_id(self, obj_string: str) -> str:
        if not obj_string:
            return obj_string
        for site_id in self.site_ids:
            if site_id in obj_string:
                return site_id
        return obj_string.split("-")[0]


# ── Fase 2: HTTP ──────────────────────────────────────────────────────

class SessionExpiredError(Exception):
    """Exceção levantada quando a sessão do iManager expira."""
    pass


class HttpCollector(BaseCollector):
    """
    FASE 2: Coleta via requisições HTTP ao iManager com renovação de sessão automática.
    """

    KPI_COLUMN_MAP = {
        "utilization_dl":    ["DL PRB USAGE", "DL PRB Usage", "PRB_Utilization"],
        "traffic_volume_dl": ["Traffic Volume DL", "{BRDC} Traffic Volume DL LTE", "Traffic Volume DL LTE", "{BRDC} NR DL Traffic Volume", "NR DL Traffic Volume", "01_L.Thrp.bits.DL"],
        "traffic_volume_ul": ["Traffic Volume UL", "{BRDC} Traffic Volume UL LTE", "Traffic Volume UL LTE", "{BRDC} NR UL Traffic Volume", "NR UL Traffic Volume", "01_L.Thrp.bits.UL"],
        "throughput_dl":     ["DL User Throughput", "{BRDC} DL User Throughput", "{BRDC} DL User Throughput LTE", "DL User Throughput LTE", "{BRDC} NR DL User Throughput", "NR DL User Throughput", "Throughput_DL_User_avg"],
        "throughput_ul":     ["UL User Throughput", "{BRDC} UL User Throughput", "{BRDC} UL User Throughput LTE", "UL User Throughput LTE", "{BRDC} NR UL User Throughput", "NR UL User Throughput", "Throughput_UL_User_avg"],
        "user_count":        ["{BRDC} Usuario", "Usuario", "Active Users", "{BRDC} User PCell", "01_L.Traffic.User.Avg"],
        "accessibility":     ["{BRDC} Acessibilidade", "Acessibilidade RRC", "ACC RRC", "Accessibility", "Disponibilidade"],
    }

    # O estado persiste entre instâncias, mas sempre é isolado por (host, módulo).
    _renew_failures: dict = {}
    _renew_backoff_until: dict = {}
    # Compatibilidade com processos iniciados em versões anteriores. Novos fluxos
    # nunca preenchem este mapa: bloqueios headless são transitórios e usam backoff.
    _needs_interactive: dict = {}
    _VIP_MAX_WORKERS = 3

    # Diagnóstico HTTP opt-in: quando ligado (HttpCollector.http_debug=True ou env
    # SMARTEVENTS_HTTP_DEBUG=1), cada requisição das sessões monitoring/trace é logada
    # (método, status, URL, presença de roarand/bspsession, content-type, body[:600]).
    # Default DESLIGADO — não afeta o caminho de produção. Usado pelo validador standalone
    # (tools/oss_validate.py) para expor a causa dos 404 (ver relatorio-erro-404.md, hipótese 3).
    http_debug: bool = False

    def __init__(self, event_config: dict, base_url: str, session_cookie: str = ""):
        super().__init__(event_config)
        self.base_url = base_url.rstrip("/")
        self._session_host = self._normalize_session_host(self.base_url)
        self.session_cookie = session_cookie
        self._session_file = self._resolve_session_file(self.base_url)
        self._session_monitoring = None
        self._session_trace = None
        # roarand com que cada sessão em cache foi construída — usado para detectar
        # quando o session.json foi renovado externamente (outra thread / reauth do
        # operador) e a sessão em cache ficou obsoleta.
        self._session_built_roarand: dict = {}
        self._renew_lock = threading.Lock()
        # A captura manual é consumida pela próxima resposta de cada tipo. A variável
        # de ambiente continua útil para reproduções locais sem passar pela interface.
        self._raw_capture_kinds: set[str] = set()
        self._last_raw_dumps: dict[str, Path] = {}
        self._auto_dumps_monitoring = 0
        self._oss_tz_offset_min = event_config.get("oss", {}).get("timezone_offset_min", -180)
        # Cliente (TIM, Vivo, …) e regional do OSS (SP, RJ, …) — escolhem as credenciais
        # (Cliente → Regional) na renovação de sessão.
        self._region = (event_config.get("oss", {}).get("region") or "").upper()
        self._cliente = (event_config.get("oss", {}).get("cliente") or "").strip()

        # Build obj_no-to-cell-info mapping for KPIs.
        # _static_obj_nos: obj_nos explicitly defined in the event config (used in API payload).
        # _obj_to_cell: includes both static AND dynamically-discovered mappings (used for parsing only).
        self._obj_to_cell = {}
        self._static_obj_nos: set = set()
        self._cell_to_site = {}
        self._cell_metadata = {}
        self._discovered_pm_tasks: set[str] = set()
        for site in event_config.get("sites", []):
            for cell in site.get("cells", []):
                c_id = cell if isinstance(cell, str) else cell.get("id")
                if c_id:
                    self._cell_to_site[c_id] = site["id"]
                    tech = self._normalize_cell_technology(cell.get("tech") if isinstance(cell, dict) else None, c_id)
                    self._cell_metadata[c_id] = {"site_id": site["id"], "technology": tech}
                    if isinstance(cell, dict) and "obj_no" in cell:
                        obj_no = int(cell["obj_no"])
                        self._static_obj_nos.add(obj_no)
                        self._obj_to_cell[obj_no] = {
                            "cell_id": c_id,
                            "site_id": site["id"],
                            "technology": tech,
                        }

    @staticmethod
    def _normalize_session_host(base_url: str) -> str:
        parsed = urlparse((base_url or "").strip())
        return (parsed.hostname or parsed.path or "unknown").strip().lower()

    def _module_state_key(self, module: str) -> tuple[str, str]:
        return self._session_host, module

    @classmethod
    def interactive_modules_for(cls, base_url: str) -> set[str]:
        host = cls._normalize_session_host(base_url)
        return {
            module for (state_host, module) in cls._needs_interactive
            if state_host == host
        }

    @staticmethod
    def _resolve_session_file(base_url: str) -> Path:
        """Deriva o caminho do session file a partir da base_url.

        Usa db.BASE_DIR (próximo ao .exe quando congelado, workspace em dev) — e NÃO
        Path(__file__), que no onefile aponta para o _MEIPASS temporário apagado a cada
        execução. O browser_profile do Playwright (derivado de session_path.parent) precisa
        PERSISTIR entre execuções para manter o SSO quente e a renovação headless funcionando."""
        _SP_DEFAULT = "https://10.220.50.9:31943"
        if not base_url or base_url.rstrip("/") == _SP_DEFAULT.rstrip("/"):
            return db.BASE_DIR / "data" / "session.json"
        try:
            import urllib.parse
            host = urllib.parse.urlparse(base_url).hostname or base_url
            slug = host.replace(".", "_")
        except Exception:
            slug = "regional"
        return db.BASE_DIR / "data" / f"session_{slug}.json"

    def _load_session_data(self) -> dict:
        session_path = self._session_file
        if session_path.exists():
            try:
                with open(session_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Não foi possível carregar session.json: {e}")
        return {}

    def arm_raw_capture(self, *kinds: str) -> None:
        """Pede um dump da próxima resposta de cada tipo informado.

        É deliberadamente uma opção da instância do coletor, não uma alteração
        global de ambiente: o botão da interface captura um ciclo sem deixar o
        aplicativo gravando respostas indefinidamente.
        """
        self._raw_capture_kinds.update(kind for kind in kinds if kind in {"monitoring", "trace"})

    def disarm_raw_capture(self, *kinds: str) -> None:
        """Cancela uma captura manual que não chegou a receber resposta."""
        for kind in kinds:
            self._raw_capture_kinds.discard(kind)

    @staticmethod
    def _safe_raw_payload(value):
        """Remove credenciais caso um contrato inesperado as inclua no corpo."""
        blocked = {"roarand", "bspsession", "cookie", "cookies", "set-cookie", "headers", "authorization"}
        if isinstance(value, dict):
            return {
                key: HttpCollector._safe_raw_payload(item)
                for key, item in value.items()
                if str(key).lower() not in blocked
            }
        if isinstance(value, list):
            return [HttpCollector._safe_raw_payload(item) for item in value]
        return value

    def _dump_raw(self, payload, kind: str, force: bool = False) -> Optional[Path]:
        """Persiste somente o corpo de uma resposta para diagnóstico opt-in.

        Cookies e headers nunca chegam a este método. Ainda assim, removemos os
        nomes de sessão de corpos inesperados antes de serializar para evitar que
        uma alteração de contrato transforme o diagnóstico em vazamento.
        ``force`` é usado pelo próprio coletor quando um ciclo termina sem medição.
        """
        env_enabled = os.environ.get("SMARTEVENTS_CAPTURE_RAW") == "1"
        manually_armed = kind in self._raw_capture_kinds
        if not (env_enabled or manually_armed or force):
            return None
        try:
            import urllib.parse

            host = urllib.parse.urlparse(self.base_url).hostname or self._region or "oss"
            oss = re.sub(r"[^A-Za-z0-9._-]+", "_", host)
            diagnostics_dir = credentials.data_dir() / "diagnostics"
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = diagnostics_dir / f"{kind}_{oss}_{stamp}.json"
            # Duas tasks de Trace podem iniciar no mesmo segundo. Nunca sobrescreva
            # uma evidência já capturada; o primeiro arquivo conserva o nome pedido.
            suffix = 2
            while path.exists():
                path = diagnostics_dir / f"{kind}_{oss}_{stamp}_{suffix}.json"
                suffix += 1
            path.write_text(
                json.dumps(self._safe_raw_payload(payload), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            self._last_raw_dumps[kind] = path
            logger.info("[diagnostics] Resposta bruta de %s salva em %s", kind, path)
            return path
        except Exception as error:
            logger.warning("[diagnostics] Não foi possível salvar resposta bruta de %s: %s", kind, error)
            return None
        finally:
            if manually_armed:
                self._raw_capture_kinds.discard(kind)

    def _build_session(self, module: str) -> requests.Session:
        """Cria uma requests.Session com cookies e headers carregados do session.json."""
        sess = requests.Session()
        sess.verify = False
        sess_data = self._load_session_data()
        module_data = sess_data.get(module, {})
        if not module_data or not module_data.get("cookies"):
            logger.warning(
                f"[session/{module}] Seção ausente ou sem cookies no session.json — "
                "chamadas HTTP podem falhar por falta de autenticação."
            )

        # Carrega cookies. Alinhamos o domínio ao host real de self.base_url (o coletor
        # SEMPRE bate em self.base_url), garantindo que o CookieJar rígido do requests anexe
        # os cookies mesmo se o evento usar hostname/IP diferente do capturado pelo Playwright.
        # Preservamos o 'path' para não sobrescrever cookies homônimos de paths distintos.
        import urllib.parse
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        cookies = module_data.get("cookies", [])
        for cookie in cookies:
            sess.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=host or cookie.get("domain", ""),
                path=cookie.get("path", "/"),
            )

        # CSRF (roarand) — DOUBLE-SUBMIT COOKIE: o iManager exige que o header `roarand`
        # ECOE o valor do COOKIE `roarand`. O servidor compara header == cookie só no POST
        # (o GET não checa), então um header defasado passa no GET e dá 401 no POST. Por isso
        # usamos o valor do cookie `roarand`, não o campo roarand gravado à parte (que vinha
        # de uma requisição antiga e não casava com o cookie). Ver ERRORS.md 2026-06-11.
        cookie_roarand = next((c["value"] for c in cookies if c.get("name") == "roarand"), None)
        roarand = cookie_roarand or module_data.get("roarand")
        if roarand:
            sess.headers.update({"roarand": roarand})

        # Headers anti-CSRF que o iManager EXIGE em POST (validação same-origin). Sem eles,
        # o filtro CSRF rejeita o POST com 302 → /unisess/v1/auth — que o requests segue,
        # convertendo POST→GET e perdendo o corpo, terminando num 404 enganoso (rota só-POST).
        # Os GETs não são checados (por isso passavam), mas o navegador real envia esses
        # headers em TODA chamada XHR same-origin. Referer difere por módulo (PM vs FARS),
        # conforme as requisições reais capturadas em docs/references/requests/result-monitoring.txt
        # e docs/references/requests/trace/filtered-request.txt.
        _referer_path = ("/oss/access/pm/index.html" if module == "monitoring"
                         else "/omc/farswebsite/index.html")
        sess.headers.update({
            "Origin": self.base_url,
            "Referer": f"{self.base_url}{_referer_path}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        })

        # Memoriza com qual roarand esta sessão foi construída (detecção de obsolescência).
        self._session_built_roarand[module] = roarand

        # Diagnóstico HTTP opt-in (default desligado) — instrumenta cada chamada.
        if self._http_debug_enabled():
            self._install_http_debug(sess, module)
        return sess

    @staticmethod
    def _http_debug_enabled() -> bool:
        return HttpCollector.http_debug or os.environ.get("SMARTEVENTS_HTTP_DEBUG") == "1"

    def _install_http_debug(self, sess: requests.Session, module: str):
        """Anexa um response-hook que loga cada requisição desta sessão. Opt-in.
        Expõe método/status/URL + presença de roarand/bspsession + corpo, para
        diagnosticar os 404 do FARS/PM (ex.: CSRF/cookies dropados entre GET e POST —
        hipótese 3 de relatorio-erro-404.md), sem duplicar a lógica de coleta."""
        def _hook(response, *args, **kwargs):
            try:
                req = response.request
                cookie_hdr = req.headers.get("Cookie", "") or ""
                req_roarand = (req.headers.get("roarand") or "")[:12]
                def _bsp(s):
                    for part in (s or "").split(";"):
                        part = part.strip()
                        if part.lower().startswith("bspsession="):
                            return part.split("=", 1)[1][:14]
                    return ""
                req_bsp = _bsp(cookie_hdr)
                # Set-Cookie da RESPOSTA: se o servidor rotaciona o bspsession aqui, o jar do
                # requests atualiza e o roarand (estático) deixa de casar → 401 só no POST.
                set_cookie = response.headers.get("Set-Cookie", "") or ""
                resp_bsp = _bsp(set_cookie)
                resp_roarand = (response.headers.get("roarand") or "")[:12]
                ctype = response.headers.get("Content-Type", "")
                try:
                    body = (response.text or "")[:200].replace("\n", " ")
                except Exception:
                    body = "(corpo indisponível)"
                logger.info(
                    f"[http/{module}] {req.method} {response.status_code} {response.url} "
                    f"req.roarand={req_roarand!r} req.bsp={req_bsp!r} "
                    f"resp.set_bsp={resp_bsp!r} resp.roarand={resp_roarand!r} "
                    f"ctype={ctype!r} body[:200]={body!r}"
                )
            except Exception as e:
                logger.warning(f"[http/{module}] hook de diagnóstico falhou: {e}")
            return response
        sess.hooks["response"].append(_hook)

    def _get_session(self, module: str) -> requests.Session:
        """Retorna a sessão ativa para o módulo, criando se necessário."""
        if module == "monitoring":
            if self._session_monitoring is None:
                self._session_monitoring = self._build_session(module)
            return self._session_monitoring
        elif module == "trace":
            if self._session_trace is None:
                self._session_trace = self._build_session(module)
            return self._session_trace
        raise ValueError(f"Módulo desconhecido: {module}")

    def _check_session_valid(self, response: requests.Response, module: str) -> bool:
        """Verifica se a resposta indica sessão expirada."""
        if response.status_code in (401, 403):
            self._log_session_invalid(module, response, "HTTP 401/403")
            return False

        # Redirecionamento (302) para o SSO num POST com allow_redirects=False: o filtro
        # anti-CSRF/sessão do iManager rejeitou a requisição. Tratamos como sessão inválida
        # (dispara renovação) em vez de seguir o redirect e cair num 404 enganoso.
        if response.status_code in (301, 302, 303, 307, 308):
            location = (response.headers.get("Location", "") or "").lower()
            if any(k in location for k in ("unisso", "unisess", "login.action", "/auth")):
                self._log_session_invalid(module, response,
                                          f"redirecionamento {response.status_code} → SSO/auth")
                return False

        url_low = (getattr(response, 'url', '') or '').lower()
        if 'unisso' in url_low or 'login.action' in url_low:
            self._log_session_invalid(module, response, "redirecionamento HTML (SSO/CAPTCHA via URL)")
            return False
            
        ct = response.headers.get("Content-Type", "")
        if "text/html" in ct:
            body_lower = response.text[:2000].lower()
            if any(k in body_lower for k in ["login", "sso", "authentication", "session"]):
                self._log_session_invalid(module, response, "redirecionamento HTML (SSO/CAPTCHA)")
                return False
        return True

    def _log_session_invalid(self, module: str, response: requests.Response, reason: str):
        """Diagnóstico: registra status/URL/trecho do corpo quando a sessão é
        considerada inválida, para distinguir SSO-redirect × CAPTCHA × CSRF inválido
        em campo, sem precisar de novo build."""
        try:
            snippet = (response.text or "")[:200].replace("\n", " ")
        except Exception:
            snippet = "(corpo indisponível)"
        logger.warning(
            f"[session/{module}] inválida — {reason}. "
            f"status={response.status_code} url={getattr(response, 'url', '?')} "
            f"body[:200]={snippet!r}"
        )

    # ── Helpers de estado de renovação ──────────────────────────────────
    def _invalidate_session(self, module: str):
        """Descarta a requests.Session em cache para forçar releitura do session.json."""
        if module == "monitoring":
            self._session_monitoring = None
        elif module == "trace":
            self._session_trace = None

    def _probe_session(self, module: str) -> bool:
        """Valida a sessão recém-carregada no contrato do próprio módulo.

        Uma troca de ``roarand`` apenas indica que o arquivo mudou. Ela não prova
        que os cookies foram autenticados no PM ou no FARS. O probe é deliberadamente
        sem efeito colateral e falha fechado: exige HTTP 200 e o envelope JSON que a
        regional deve devolver para aquele módulo.
        """
        session = self._get_session(module)
        try:
            if module == "monitoring":
                response = session.get(
                    f"{self.base_url}/rest/oss/access/pm/v1/monitor/task/view-tree",
                    timeout=30,
                    allow_redirects=False,
                )
            elif module == "trace":
                session_data = self._load_session_data()
                task_id = (
                    next(iter(self.vips_by_task), None)
                    or (session_data.get("trace", {}) or {}).get("task_id")
                    or 1
                )
                response = session.get(
                    f"{self.base_url}/rest/oss/access/fars/v1/traceresult/pre-check",
                    params={
                        "taskId": task_id,
                        "queryType": 0,
                        "nocache": int(time.time() * 1000),
                    },
                    timeout=30,
                    allow_redirects=False,
                )
            else:
                raise ValueError(f"Módulo desconhecido: {module}")

            if response.status_code != 200 or not self._check_session_valid(response, module):
                return False
            content_type = (response.headers.get("Content-Type", "") or "").lower()
            if "json" not in content_type:
                return False
            payload = response.json()
            if module == "monitoring":
                return (
                    isinstance(payload, dict)
                    and payload.get("success") is True
                    and isinstance(payload.get("data"), list)
                )
            return (
                isinstance(payload, dict)
                and isinstance(payload.get("checkState"), bool)
            )
        except (requests.exceptions.RequestException, ValueError, TypeError, KeyError) as error:
            logger.warning("[renew/%s] probe falhou: %s", module, error)
            return False

    def _reload_and_probe(self, module: str, source: str) -> bool:
        """Recarrega o arquivo e só o aceita depois do probe do módulo."""
        self._invalidate_session(module)
        logger.info("[renew/%s] arquivo recarregado (%s).", module, source)
        if self._probe_session(module):
            logger.info("[renew/%s] probe aceito (%s).", module, source)
            return True
        logger.warning("[renew/%s] probe recusado (%s).", module, source)
        self._invalidate_session(module)
        return False

    def _clear_interactive_state(self, module: str):
        HttpCollector._needs_interactive.pop(self._module_state_key(module), None)

    @classmethod
    def reset_interactive_state(cls, module: Optional[str] = None,
                                base_url: Optional[str] = None):
        """Limpa estado de reauth somente no host informado.

        Sem ``base_url`` mantém-se o reset global explícito, útil no startup/testes.
        """
        if base_url is None and module is None:
            cls._needs_interactive.clear()
            cls._renew_failures.clear()
            cls._renew_backoff_until.clear()
            return
        host = cls._normalize_session_host(base_url or "") if base_url else None
        keys = set(cls._needs_interactive) | set(cls._renew_failures) | set(cls._renew_backoff_until)
        for key in keys:
            key_host, key_module = key
            if (host is None or key_host == host) and (module is None or key_module == module):
                cls._needs_interactive.pop(key, None)
                cls._renew_failures.pop(key, None)
                cls._renew_backoff_until.pop(key, None)

    def _engage_backoff(self, module: str) -> int:
        """Incrementa o contador de falhas e arma o backoff (60→300s). Retorna o backoff em s."""
        from datetime import timedelta
        state_key = self._module_state_key(module)
        failures = HttpCollector._renew_failures.get(state_key, 0) + 1
        HttpCollector._renew_failures[state_key] = failures
        backoff_s = min(300, 60 * failures)
        HttpCollector._renew_backoff_until[state_key] = (
            datetime.utcnow() + timedelta(seconds=backoff_s)
        )
        return backoff_s


    def _renew_session(self, module: str) -> bool:
        """
        Renova a sessão via Playwright (subprocesso headless). Retorna True somente
        quando a renovação foi EFETIVA (sessão autenticada gravada); False caso
        contrário (backoff ativo, login ainda em progresso ou falha).

        - Backoff exponencial (60→300s) após falhas consecutivas, evitando loop.
        - exit 2 (EXIT_NEEDS_INTERACTIVE): estado transitório do login. Não abre
          navegador nem pede intervenção; agenda nova tentativa headless.
        - Se outra thread renovar o session file, recarrega e executa o probe do
          módulo antes de aceitar.
        """
        state_key = self._module_state_key(module)
        # Versões anteriores pausavam indefinidamente e abriam um navegador quando
        # o login headless encontrava um bloqueio transitório. Esse estado não deve
        # sobreviver: a renovação é sempre automática e volta a tentar com backoff.
        if state_key in HttpCollector._needs_interactive:
            logger.info(
                "[renew/%s] Estado manual legado descartado; retomando renovação automática.",
                module,
            )
            self._clear_interactive_state(module)

        # ── Backoff: se falhou recentemente, pula esta tentativa ─────────
        now = datetime.utcnow()
        backoff_until = HttpCollector._renew_backoff_until.get(state_key)
        if backoff_until and now < backoff_until:
            remaining = int((backoff_until - now).total_seconds())
            logger.warning(
                f"[renew/{module}] Backoff ativo — pulando renovação por mais {remaining}s "
                f"({HttpCollector._renew_failures.get(state_key, 0)} falha(s) consecutiva(s))."
            )
            return False

        # ── Sessão em cache obsoleta? Se o session.json já tem um roarand diferente do
        #    que a sessão em cache usou, outra thread publicou uma renovação:
        #    recarregue do arquivo e valide antes de iniciar outro Playwright. ────────
        _mod_data = self._load_session_data().get(module, {}) or {}
        _file_roarand = _mod_data.get("roarand")
        if (_file_roarand and _file_roarand != self._session_built_roarand.get(module)
                and _mod_data.get("cookies")):
            if self._reload_and_probe(module, "arquivo renovado externamente"):
                return True
            # Mudança sem autenticação comprovada: segue para o Playwright.

        # ── Captura roarand ANTES do lock para detectar renovação concorrente ──
        roarand_before = self._load_session_data().get(module, {}).get("roarand")

        with self._renew_lock:
            # Se outro thread renovou enquanto esperávamos, verificar se a seção
            # deste módulo ainda está válida no arquivo gravado
            current_data = self._load_session_data()
            roarand_now = current_data.get(module, {}).get("roarand")
            if roarand_now and roarand_now != roarand_before:
                module_data = current_data.get(module, {})
                has_valid = bool(module_data.get("cookies")) and bool(module_data.get("roarand"))
                if has_valid:
                    if self._reload_and_probe(module, "arquivo renovado por outra thread"):
                        return True
                    logger.warning(
                        f"[renew/{module}] Outra thread publicou uma sessão que o probe "
                        "recusou. Executando Playwright para este módulo."
                    )
                else:
                    logger.warning(
                        f"[renew/{module}] Outra thread renovou mas a seção '{module}' está "
                        "ausente/incompleta no session.json. Executando Playwright para este módulo."
                    )

            try:
                db.insert_alert({
                    "event_id":  self.event_id,
                    "level":     "GLOBAL",
                    "severity":  "WARNING",
                    "site_id":   "GLOBAL",
                    "cell_id":   "",
                    "message":   f"Sessão do módulo '{module}' expirada. Renovando automaticamente...",
                    "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                })
            except Exception as e:
                logger.warning(f"Não foi possível inserir alerta de sessão expirada: {e}")

            # Renovação em SUBPROCESSO isolado (um processo limpo por renovação). Evita os problemas
            # da Sync API do Playwright entre as threads de coleta (monitoring/trace) e vazamento de
            # processos node.exe ao longo do tempo. No .exe, o próprio executável renova via
            # `--get-session` (Playwright + Chromium empacotados); em dev, usa o Python do venv.
            if getattr(sys, "frozen", False):
                renew_cmd = [sys.executable, "--get-session"]
            else:
                python_exe = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"
                if not python_exe.exists():
                    python_exe = Path("python")
                script_path = Path(__file__).parent.parent / "scratch" / "get_session.py"
                renew_cmd = [str(python_exe), str(script_path)]

            logger.info(f"[renew/{module}] Playwright usado (subprocesso, timeout=120s).")
            try:
                result = subprocess.run(
                    renew_cmd + [
                        "--headless",
                        "--module", module,
                        "--base-url", self.base_url,
                        "--session-file", str(self._session_file),
                        "--region", self._region,
                        "--cliente", self._cliente,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(Path(__file__).parent.parent),
                )
                stdout_tail = result.stdout.strip()[-2000:] if result.stdout.strip() else ""
                stderr_tail = result.stderr.strip()[-500:] if result.stderr.strip() else ""

                if result.returncode == EXIT_SUCCESS:
                    if stdout_tail:
                        logger.debug(f"[renew/{module}] stdout:\n{stdout_tail}")
                    # Cookies são compartilhados, mas cada módulo mantém seu
                    # próprio requests.Session. Reconstrói ambos para não deixar
                    # o outro worker usando headers/cookies anteriores.
                    self._invalidate_session("monitoring")
                    self._invalidate_session("trace")
                    if self._reload_and_probe(module, "sessão publicada pelo Playwright"):
                        HttpCollector._renew_failures[state_key] = 0
                        HttpCollector._renew_backoff_until.pop(state_key, None)
                        self._clear_interactive_state(module)
                        return True
                    backoff_s = self._engage_backoff(module)
                    logger.error(
                        f"[renew/{module}] Playwright terminou, mas o probe do módulo "
                        f"recusou a sessão. Próxima tentativa em {backoff_s}s."
                    )
                    return False

                if result.returncode == EXIT_NEEDS_INTERACTIVE:
                    # CAPTCHA/SSO e estados intermediários do login são transitórios.
                    # Nunca transfira a responsabilidade ao operador: aguarde e tente
                    # novamente de forma headless no próximo ciclo.
                    self._clear_interactive_state(module)
                    backoff_s = self._engage_backoff(module)
                    logger.warning(
                        f"[renew/{module}] Login headless ainda não concluiu "
                        f"(estado transitório). Nova tentativa automática em {backoff_s}s; "
                        f"nenhuma ação manual é necessária.\n"
                        f"  STDOUT: {stdout_tail or '(vazio)'}\n  STDERR: {stderr_tail or '(vazio)'}"
                    )
                    return False

                # Falha genérica (EXIT_GENERIC_FAIL ou returncode inesperado)
                backoff_s = self._engage_backoff(module)
                failures = HttpCollector._renew_failures.get(state_key, 0)
                logger.error(
                    f"[renew/{module}] Renovação falhou "
                    f"(returncode={result.returncode}, tentativa #{failures}). "
                    f"Próxima tentativa em {backoff_s}s.\n"
                    f"  STDOUT: {stdout_tail or '(vazio)'}\n"
                    f"  STDERR: {stderr_tail or '(vazio)'}"
                )
                return False
            except subprocess.TimeoutExpired:
                backoff_s = self._engage_backoff(module)
                failures = HttpCollector._renew_failures.get(state_key, 0)
                logger.error(
                    f"[renew/{module}] Renovação TIMEOUT após 120s "
                    f"(tentativa #{failures}). Próxima tentativa em {backoff_s}s."
                )
                return False
            except Exception as e:
                backoff_s = self._engage_backoff(module)
                failures = HttpCollector._renew_failures.get(state_key, 0)
                logger.error(
                    f"[renew/{module}] Erro ao executar a renovação "
                    f"(tentativa #{failures}): {e}. Próxima tentativa em {backoff_s}s."
                )
                return False

    def _legacy_collect_kpis(self) -> CollectionResult:
        integration = self.event.get("integration", {})
        sess_data = self._load_session_data()
        # Prioritize event-specific pm_task_id over session task_id to prevent mixups
        task_id = integration.get("pm_task_id") or sess_data.get("monitoring", {}).get("task_id") or 374

        # Event-isolated cell discovery:
        # If we have not yet dynamically mapped all cells of the event to objNos, we query
        # with objNoExecTimes = [] (empty list) to retrieve all objects in the task and discover them.
        # Once all cells are mapped, we query only our specific obj_nos to retrieve newer data.
        if len(self._obj_to_cell) < len(self.cell_ids):
            obj_nos = []
        else:
            obj_nos = list(self._static_obj_nos) or list(self._obj_to_cell.keys())

        now_ms = int(time.time() * 1000)
        payload = [{
            "taskId": task_id,
            "preExecTime": now_ms,
            "objNoExecTimes": [{"preExecTime": now_ms, "objNo": obj_no} for obj_no in obj_nos] if obj_nos else []
        }]

        # POST sem ?nocache= (limpeza inócua — o corpo já leva preExecTime fresco). NÃO era a
        # causa do 404/401: a causa real era CSRF (header roarand ≠ cookie roarand). Ver ERRORS.md.
        url = f"{self.base_url}/rest/oss/access/pm/v1/monitor/task/result"
        retries = 2
        renewed_this_call = False
        for attempt in range(retries):
            s = self._get_session("monitoring")
            try:
                resp = s.post(url, json=payload, timeout=30,
                              headers={"x-non-renewal-session": "true"}, allow_redirects=False)
                if not self._check_session_valid(resp, "monitoring"):
                    raise SessionExpiredError(f"Redirecionamento para SSO detectado no módulo monitoring")
                resp.raise_for_status()
                data = resp.json()
                measurements = self._parse_kpi_response(data)
                coverage = {
                    "cells_mapped": len(self._obj_to_cell),
                    "cells_expected": len(self.cell_ids),
                }
                latest_data_at = max((m.get("timestamp") for m in measurements), default=None)
                if len(self._obj_to_cell) < len(self.cell_ids):
                    return CollectionResult.partial(
                        measurements,
                        cause="Nem todas as células configuradas foram localizadas no Monitoring.",
                        coverage=coverage,
                        latest_data_at=latest_data_at,
                    )
                if measurements:
                    return CollectionResult.data(measurements, coverage=coverage,
                                                 latest_data_at=latest_data_at)
                return CollectionResult.empty("Monitoring respondeu sem medições novas.",
                                              coverage=coverage)
            except SessionExpiredError as e:
                logger.warning(f"Erro de sessão no collect_kpis: {e}")
                if renewed_this_call:
                    # Já renovamos nesta chamada e a sessão renovada AINDA é inválida.
                    # Não renovar de novo (evita loop): engata backoff e para por aqui.
                    self._engage_backoff("monitoring")
                    logger.error("Sessão monitoring renovada mas ainda inválida — pausando coleta de KPIs (backoff).")
                    return CollectionResult.auth_required(
                        "A sessão de Monitoring continuou inválida após a renovação.")
                if attempt < retries - 1:
                    logger.warning("Sessão do módulo monitoring expirada. Iniciando renovação automática...")
                    if self._renew_session("monitoring"):
                        renewed_this_call = True
                        continue
                    # Renovação não efetiva (backoff/CAPTCHA): não adianta retentar agora.
                    return CollectionResult.auth_required(
                        "Não foi possível renovar a sessão de Monitoring.")
                return CollectionResult.auth_required("A sessão de Monitoring expirou.")
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                logger.error(f"Erro de conexão ao consultar KPIs: {e}")
                try:
                    active_alerts = db.get_active_alerts(self.event_id)
                    conn_msg = "Falha de conexão com o iManager (KPIs). Verifique a VPN do cliente."
                    if not any(a.get("message") == conn_msg for a in active_alerts):
                        db.insert_alert({
                            "event_id":  self.event_id,
                            "level":     "GLOBAL",
                            "severity":  "CRITICAL",
                            "site_id":   "GLOBAL",
                            "cell_id":   "",
                            "message":   conn_msg,
                            "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                        })
                except Exception as alert_ex:
                    logger.warning(f"Não foi possível inserir alerta de conexão: {alert_ex}")
                return CollectionResult.error(f"Falha de conexão ao consultar KPIs: {e}", code="network")
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP ao consultar KPIs: {e}")
                return CollectionResult.error(f"Falha HTTP ao consultar KPIs: {e}", code="http")
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Resposta inválida do Monitoring: {e}")
                return CollectionResult.error(f"Resposta inválida do Monitoring: {e}", stage="parsing", code="contract")
        return CollectionResult.error("Monitoring terminou sem resposta.", code="unknown")

    def _resolve_monitoring_cell(self, obj_no: int, obj_name: str, technology: Optional[str]) -> Optional[dict]:
        known = self._obj_to_cell.get(obj_no)
        if known:
            return known
        if not technology:
            return None
        # O nome do objeto contém diversos atributos. Consideramos somente o valor
        # explícito de "Cell Name", comparado após normalização estável; não há
        # matching amplo por substring de site/célula.
        match = re.search(r"Cell Name\s*=\s*([^,]+)", obj_name or "", re.I)
        candidate = self._normalized_name(match.group(1) if match else obj_name)
        # A tecnologia da célula é inferida do NOME (``_normalize_cell_technology``) e nem todo
        # OSS a carrega ali: em SP as células chamam-se ``4G-SPSMG7-18-C`` (token explícito),
        # no OSS de Curitiba chamam-se ``18NLCTAL01GI`` — sem 4G/5G no nome, a tecnologia sai
        # ``None`` e o casamento por igualdade com a tecnologia da task nunca acontecia: 100%
        # dos objetos ficavam não mapeados. Célula de tecnologia DESCONHECIDA é candidata a
        # qualquer task (quem define a tecnologia do dado é a task consultada); célula de
        # tecnologia CONHECIDA e diferente continua fora — essa é a troca silenciosa de 4G por
        # 5G que o gate existe para impedir.
        choices = [cell_id for cell_id, metadata in self._cell_metadata.items()
                   if metadata.get("technology") in (technology, None)
                   and self._normalized_name(cell_id) == candidate]
        if len(choices) != 1:
            return None
        cell_id = choices[0]
        # A task é a fonte da tecnologia do dado. Gravá-la aqui faz os ciclos seguintes
        # pedirem este objNo explicitamente (ver _request_objects_for_task).
        result = {**self._cell_metadata[cell_id], "cell_id": cell_id, "technology": technology}
        self._obj_to_cell[obj_no] = result
        return result

    @staticmethod
    def _counter_map(counter_res) -> tuple[dict[str, float], dict[str, str]]:
        values, invalid = {}, {}
        for counter in counter_res or []:
            if not isinstance(counter, dict) or not counter.get("name"):
                continue
            name = str(counter["name"])
            reliable = counter.get("reliable", 1)
            if reliable not in (1, 1.0, True, "1", "1.0", "true", "True"):
                invalid[name] = "contador não confiável"
                continue
            try:
                values[name] = float(counter["value"])
            except (KeyError, TypeError, ValueError):
                invalid[name] = "contador não numérico"
        return values, invalid

    @staticmethod
    def _timestamp_from_exec_time(exec_time) -> str:
        if exec_time:
            return datetime.utcfromtimestamp(float(exec_time) / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ")
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    def _site_rows(self, source: list[dict]) -> list[dict]:
        grouped = {}
        for row in source:
            key = (row["site_id"], row["timestamp"], row["technology"], row["metric"])
            grouped.setdefault(key, []).append(row)
        rows = []
        for (site_id, timestamp, technology, metric), items in grouped.items():
            definition = next((item for item in definitions_for(technology) if item.id == metric), None)
            if not definition:
                continue
            values = [item["value"] for item in items]
            # Para percentuais compostos, ``source`` recebe o resultado de cada
            # célula; o parser recalcula-os antes de chegar aqui quando há raw
            # counters homogêneos. As demais regras são matematicamente definidas.
            if definition.site_aggregation == "sum":
                value = sum(values)
            elif definition.site_aggregation == "mean":
                value = sum(values) / len(values)
            else:
                # Percentuais compostos são adicionados pelo parser a partir dos
                # contadores somados; nunca use média simples de percentuais.
                continue
            rows.append({"event_id": self.event_id, "site_id": site_id, "cell_id": "__site__",
                         "timestamp": timestamp, "metric": metric, "value": value,
                         "scope": "SITE", "technology": technology})
        return rows

    @staticmethod
    def _obj_field(item: dict, obj: dict, *names):
        """Lê um atributo do objeto aceitando as duas grafias do iManager.

        O OSS de SP identifica a célula em ``objRes[].obj.objNo``/``objName``; o de
        Curitiba usa ``objectNo``/``objectName`` e ainda devolve ``objName: null`` no
        mesmo dicionário — ler só a primeira grafia derrubava 100% dos objetos em
        ``int(None)``, contados como inválidos antes mesmo de ``received``.
        """
        for source in (item, obj):
            for name in names:
                value = source.get(name)
                if value not in (None, ""):
                    return value
        return None

    def _parse_monitoring_response(self, response_json: dict, task_technologies: dict[str, str]) -> dict:
        from core.collection_result import CollectionDiagnostic

        rows, source, diagnostics = [], [], []
        task_cursor_candidates, object_cursor_candidates = {}, {}
        persisted_object_keys = set()
        site_counter_groups = {}
        received = invalid = unmapped = 0
        unmapped_cells = []
        latest_data_at = None
        for task_data in response_json.get("data") or []:
            task_id = task_data.get("taskId")
            technology = task_technologies.get(str(task_id))
            if not technology:
                diagnostics.append(CollectionDiagnostic("contract", f"Task PM inesperada: {task_id}", "unknown_task"))
                continue
            task_cursor = task_data.get("execTime")
            if task_cursor is not None:
                task_cursor_candidates[task_id] = task_cursor
            for checkpoint in task_data.get("objNoExecTimes") or []:
                if checkpoint.get("objNo") is not None and checkpoint.get("preExecTime") is not None:
                    object_cursor_candidates[(task_id, str(checkpoint["objNo"]))] = checkpoint["preExecTime"]
            for result in task_data.get("results") or []:
                timestamp = self._timestamp_from_exec_time(result.get("execTime") or task_cursor)
                latest_data_at = max(latest_data_at or timestamp, timestamp)
                items = result.get("objRes", []) if "objRes" in result else [result]
                for item in items:
                    obj = item.get("obj") or {}
                    obj_no = self._obj_field(item, obj, "objNo", "objectNo")
                    try:
                        obj_no = int(obj_no)
                    except (TypeError, ValueError):
                        invalid += 1
                        diagnostics.append(CollectionDiagnostic("parsing", "Objeto Monitoring sem objNo válido", "missing_obj_no"))
                        continue
                    received += 1
                    obj_name = self._obj_field(item, obj, "objName", "objectName") or ""
                    info = self._resolve_monitoring_cell(obj_no, obj_name, technology)
                    if not info:
                        unmapped += 1
                        # O nome registrado é o MESMO que a resolução tentou casar; antes
                        # daqui saía o objNo quando o objName vinha no item (e não no obj),
                        # o que tornava o diagnóstico inútil justamente no caso a diagnosticar.
                        unmapped_cells.append(obj_name or str(obj_no))
                        continue
                    counters, counter_errors = self._counter_map(item.get("counterRes"))
                    period = result.get("period") or task_data.get("period")
                    site_counter_groups.setdefault((info["site_id"], timestamp, technology), []).append((counters, period))
                    per_metric = []
                    for definition in definitions_for(technology):
                        try:
                            value = calculate_kpi(definition, counters, float(period) if period is not None else None)
                        except InvalidKpi as error:
                            invalid += 1
                            diagnostics.append(CollectionDiagnostic("formula", str(error), "invalid_formula", {
                                "metric": definition.id, "cell_id": info["cell_id"], "technology": technology,
                            }))
                            continue
                        measurement = {"event_id": self.event_id, "site_id": info["site_id"], "cell_id": info["cell_id"],
                                       "timestamp": timestamp, "metric": definition.id, "value": value,
                                       "scope": "CELL", "technology": technology}
                        rows.append(measurement)
                        per_metric.append((definition, measurement))
                    # Checkpoint é uma confirmação de persistência, não apenas
                    # de que o nome do objeto foi reconhecido. Fórmulas sem
                    # linha válida também precisam poder ser reprocessadas.
                    if per_metric:
                        persisted_object_keys.add((task_id, str(obj_no)))
                    for name, reason in counter_errors.items():
                        diagnostics.append(CollectionDiagnostic("counter", reason, "invalid_counter", {"counter": name, "cell_id": info["cell_id"]}))
                    source.extend(per_metric)
        rows.extend(self._site_rows([row for _, row in source]))
        # Percentuais compostos usam os contadores somados no mesmo timestamp.
        # ``period`` é multiplicado pelo número de células para availability,
        # preservando GP/SP devolvido pelo OSS em vez do intervalo local.
        for (site_id, timestamp, technology), samples in site_counter_groups.items():
            summed = {}
            for counters, _ in samples:
                for name, value in counters.items():
                    summed[name] = summed.get(name, 0.0) + value
            periods = [float(period) for _, period in samples if period is not None]
            site_period = periods[0] * len(samples) if periods else None
            for definition in definitions_for(technology):
                if definition.site_aggregation != "recalculate":
                    continue
                try:
                    value = calculate_kpi(definition, summed, site_period)
                except InvalidKpi:
                    continue
                rows.append({"event_id": self.event_id, "site_id": site_id, "cell_id": "__site__",
                             "timestamp": timestamp, "metric": definition.id, "value": value,
                             "scope": "SITE", "technology": technology})
        cursors = {
            f"{task_id}:{object_key}": {"task_id": task_id, "object_key": object_key, "cursor": cursor}
            for (task_id, object_key), cursor in object_cursor_candidates.items()
            if (task_id, object_key) in persisted_object_keys
        }
        if not unmapped:
            cursors.update({
                f"{task_id}:": {"task_id": task_id, "object_key": "", "cursor": cursor}
                for task_id, cursor in task_cursor_candidates.items()
            })
        return {"rows": rows, "received": received, "invalid": invalid, "unmapped": unmapped,
                "unmapped_cells": sorted(set(unmapped_cells)), "diagnostics": diagnostics,
                "cursors": cursors, "latest_data_at": latest_data_at}

    def _parse_kpi_response(self, response_json: dict) -> List[dict]:
        rows = []
        # Acumuladores para um único log-resumo da coleta (em vez de logar item a item).
        mapped_now: list = []   # células mapeadas dinamicamente neste ciclo
        objs_seen = 0           # objetos retornados pela task de monitoring
        data_list = response_json.get("data", [])
        if not data_list:
            logger.info("Monitoring: nenhuma medição retornada neste ciclo.")
            return rows

        for task_data in data_list:
            results = task_data.get("results", [])
            for res_item in results:
                # If the response has "objRes", iterate over the items inside it.
                # Otherwise, treat the res_item itself as the object measurement.
                items_to_process = res_item.get("objRes", []) if "objRes" in res_item else [res_item]

                exec_time = res_item.get("execTime") or task_data.get("execTime")
                if exec_time:
                    timestamp = datetime.utcfromtimestamp(exec_time / 1000.0).strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

                for item in items_to_process:
                    objs_seen += 1
                    obj_data = item.get("obj") or {}
                    obj_no = item.get("objNo") or obj_data.get("objNo") or (item.get("obj", {}).get("objNo") if isinstance(item.get("obj"), dict) else None)
                    if not obj_no:
                        continue

                    try:
                        obj_no_int = int(obj_no)
                    except (ValueError, TypeError):
                        continue

                    cell_info = self._obj_to_cell.get(obj_no_int)

                    if not cell_info:
                        obj_name = item.get("objName") or obj_data.get("objName") or ""
                        if obj_name:
                            obj_name_clean = obj_name.replace("-", "").replace(" ", "").upper()
                            for c_id, s_id in self._cell_to_site.items():
                                c_id_clean = c_id.replace("-", "").replace(" ", "").upper()
                                if c_id in obj_name or c_id_clean in obj_name_clean:
                                    cell_info = {
                                        "cell_id": c_id,
                                        "site_id": s_id
                                    }
                                    self._obj_to_cell[obj_no_int] = cell_info
                                    mapped_now.append(c_id)
                                    break

                    if not cell_info:
                        continue

                    for metric, candidates in self.KPI_COLUMN_MAP.items():
                        val, matched_cand = self._find_metric_value_and_candidate(item, candidates)
                        if val is not None:
                            try:
                                float_val = float(val)
                                
                                # Scale RJ-specific counters
                                if matched_cand in ("01_L.Thrp.bits.DL", "01_L.Thrp.bits.UL"):
                                    float_val = float_val / (8.0 * 1024.0 * 1024.0)  # bits to MB
                                elif matched_cand in ("Throughput_DL_User_avg", "Throughput_UL_User_avg"):
                                    float_val = float_val / 1000.0  # kbps to Mbps
                                
                                rows.append({
                                    "site_id":   cell_info["site_id"],
                                    "cell_id":   cell_info["cell_id"],
                                    "event_id":  self.event_id,
                                    "timestamp": timestamp,
                                    "metric":    metric,
                                    "value":     float_val,
                                })
                            except (ValueError, TypeError):
                                pass

        # Log-resumo único do ciclo de monitoring (em vez de logar item a item).
        msg = (
            f"Monitoring: {len(rows)} medições coletadas de {objs_seen} objeto(s); "
            f"{len(self._obj_to_cell)}/{len(self.cell_ids)} células do evento mapeadas"
        )
        if mapped_now:
            msg += f" (+{len(mapped_now)} mapeada(s) dinamicamente neste ciclo)"
        logger.info(msg + ".")
        return rows

    def _find_metric_value_and_candidate(self, d, candidates):
        if not isinstance(d, dict):
            return None, None

        # Support iManager counterRes structures: {"name": "...", "value": "..."}
        if d.get("name") in candidates and "value" in d:
            return d.get("value"), d.get("name")

        for k, v in d.items():
            if k in candidates:
                return v, k
        for k, v in d.items():
            if isinstance(v, dict):
                val, cand = self._find_metric_value_and_candidate(v, candidates)
                if val is not None:
                    return val, cand
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        val, cand = self._find_metric_value_and_candidate(item, candidates)
                        if val is not None:
                            return val, cand
        return None, None

    def _find_metric_value(self, d, candidates):
        val, _ = self._find_metric_value_and_candidate(d, candidates)
        return val

    # O filtro por Message Type é aplicado no servidor e o RSRP/RSRQ sai do
    # messageBody por decodificação local, então um ciclo custa uma requisição
    # por página — não uma por mensagem.  O teto de páginas só impede que um
    # bootstrap muito longo bloqueie o worker; o restante vira backlog.
    _VIP_PAGE_SIZE = 1000
    _VIP_MAX_PAGES_PER_CYCLE = 5
    # A alocação do msgId espelha a página que o navegador pede no mesmo passo.
    _VIP_BOOTSTRAP_PAGE_SIZE = 100

    def _trace_oss(self) -> str:
        return (self._region or self.base_url).upper()

    def _open_trace_query(self, session, task_id: int) -> tuple[int, Optional[str], Optional[str]]:
        """Abre a consulta FARS e devolve ``msgId`` e a janela da task do ciclo.

        O ``msgId`` é um handle descartável: cada chamada de ``query/result`` com
        ``msgId=1`` aloca um novo no servidor. Por isso ele precisa ser criado e
        consumido dentro do mesmo ciclo — reaproveitar um handle de outro ciclo
        faz o FARS responder sem linhas.

        ``fetch-field-values`` devolve o intervalo coberto pela task. Ele não é
        opcional: ``filter-by-cols`` responde HTTP 500 quando recebe
        ``startTime``/``endTime`` vazios.
        """
        pre = session.get(
            f"{self.base_url}/rest/oss/access/fars/v1/traceresult/pre-check",
            params={"taskId": task_id, "queryType": 0, "nocache": int(time.time() * 1000)},
            timeout=30, allow_redirects=False,
        )
        if not self._check_session_valid(pre, "trace"):
            raise SessionExpiredError("Sessão Trace expirada no pre-check")
        pre.raise_for_status()
        pre_data = pre.json() if pre.content else {}
        if not isinstance(pre_data, dict) or not pre_data.get("checkState", False):
            raise ValueError(f"Task {task_id} ausente, parada ou recusada pelo pre-check")

        initial = session.get(
            f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/result",
            params={
                "nocache": int(time.time() * 1000), "startRow": 0,
                "pageSize": self._VIP_BOOTSTRAP_PAGE_SIZE,
                "taskId": task_id, "msgId": 1, "isSetBenchMarkTime": "false",
                "benchMarkTimeRowNo": -1,
            }, timeout=60, allow_redirects=False,
        )
        if not self._check_session_valid(initial, "trace"):
            raise SessionExpiredError("Sessão Trace expirada ao abrir a consulta")
        initial.raise_for_status()
        initial_payload = initial.json()
        data = initial_payload.get("data") or {}
        if not isinstance(data, dict) or data.get("msgId") is None:
            raise ValueError("A abertura da consulta de Trace não retornou msgId")
        msg_id = int(data["msgId"])
        # Algumas regionais devolvem HTTP 500 em fetch-field-values quando a
        # task ainda não tem nenhuma mensagem. O recordCount do bootstrap já é
        # conclusivo e permite encerrar o ciclo vazio sem transformar isso em
        # falha de contrato.
        if "recordCount" in initial_payload:
            try:
                if int(initial_payload.get("recordCount") or 0) == 0:
                    return msg_id, None, None
            except (TypeError, ValueError):
                pass

        fields = session.get(
            f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/fetch-field-values",
            params={"nocache": int(time.time() * 1000), "taskId": task_id, "msgId": msg_id},
            timeout=60, allow_redirects=False,
        )
        if not self._check_session_valid(fields, "trace"):
            raise SessionExpiredError("Sessão Trace expirada ao ler a janela da task")
        fields.raise_for_status()
        window = fields.json() if fields.content else {}
        start_time, end_time = window.get("startTime"), window.get("endTime")
        if not start_time or not end_time:
            raise ValueError(f"Task {task_id} não informou a janela de tempo (startTime/endTime)")
        return msg_id, start_time, end_time

    @staticmethod
    def _allocated_msg_id(payload, endpoint: str) -> tuple[int, int]:
        """Extrai o ``msgId`` alocado e o ``recordCount`` de uma resposta do FARS.

        Todo passo que muda o conjunto (``filter-by-cols``, ``sort``) materializa
        um snapshot novo e devolve o handle dele em ``data.msgId`` — o handle de
        entrada continua apontando para o conjunto anterior.
        """
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError(f"{endpoint} retornou contrato inválido")
        try:
            msg_id = int(data["msgId"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{endpoint} não devolveu msgId")
        try:
            total = int(payload.get("recordCount") or 0)
        except (TypeError, ValueError):
            total = 0
        return msg_id, total

    def _filter_meas_reports(self, session, task_id: int, msg_id: int,
                             start_time: str, end_time: str) -> tuple[int, int]:
        """Filtra ``RRC_MEAS_RPRT`` no servidor e devolve ``(msgId filtrado, total)``.

        Chamado **uma vez por ciclo**. As linhas vêm depois pelo ``result-paging``
        sobre o handle devolvido aqui: repetir este POST por página re-executaria
        o filtro contra os dados vivos, e duas páginas do mesmo ciclo poderiam sair
        de snapshots diferentes.
        """
        body = {
            "colFilterDto": {
                "colFltExpSeq": [
                    {"fieldId": "Message Type", "value": "RRC_MEAS_RPRT", "operator": {"op": 0}}
                ],
                # ``hasStartTime``/``hasEndTime`` falsos usam a janela inteira,
                # mas as datas precisam vir preenchidas — o servidor as desserializa
                # de qualquer forma e devolve 500 se vierem vazias.
                "signalList": [], "hasStartTime": False, "startTime": start_time,
                "hasEndTime": False, "endTime": end_time, "isReverse": False,
            },
            "pageDto": {
                "sqlColumnName": "", "isAscend": "", "taskId": task_id, "msgId": msg_id,
                "comparisonMsgId": -1, "startRow": 0, "pageSize": self._VIP_PAGE_SIZE,
                "templateName": [], "isSetBenchMarkTime": False, "benchMarkTimeRowNo": -1,
            },
        }
        response = session.post(
            f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols",
            params={"nocache": int(time.time() * 1000)}, json=body,
            timeout=120, allow_redirects=False,
        )
        if not self._check_session_valid(response, "trace"):
            raise SessionExpiredError("Sessão Trace expirada no filtro de mensagens")
        response.raise_for_status()
        return self._allocated_msg_id(response.json(), "filter-by-cols")

    def _sort_trace_by_time(self, session, task_id: int, msg_id: int) -> tuple[int, int]:
        """Ordena o conjunto filtrado por ``Time`` e devolve ``(msgId ordenado, total)``.

        O conjunto que sai do ``filter-by-cols`` **não vem ordenado** — na captura
        da task 2072 são 127 violações de ordem crescente em 619 linhas, em runs
        curtos que não acompanham o NE. Sobre uma ordem assim, parar a paginação no
        meio e gravar a marca d'água ``max(serialNo)`` apaga em silêncio as linhas
        de serial menor que ainda não foram lidas.

        A ordenação é **ascendente** de propósito: os dados novos entram no fim, o
        que mantém estável o prefixo já consumido e dá sentido ao cursor por offset.
        Descendente serve à tela, não a um coletor que retoma de onde parou.
        """
        body = {
            "msgId": msg_id, "comparisonMsgId": -1, "taskId": task_id,
            "isAscend": True, "sqlColumnName": "Time",
            "startRow": 0, "pageSize": self._VIP_PAGE_SIZE, "templateName": [],
            "isSetBenchMarkTime": False, "benchMarkTimeRowNo": -1,
        }
        response = session.post(
            f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/sort",
            params={"nocache": int(time.time() * 1000)}, json=body,
            timeout=120, allow_redirects=False,
        )
        if not self._check_session_valid(response, "trace"):
            raise SessionExpiredError("Sessão Trace expirada na ordenação")
        response.raise_for_status()
        return self._allocated_msg_id(response.json(), "query/sort")

    def _fetch_trace_page(self, session, task_id: int, msg_id: int, start_row: int,
                          capture_raw: bool = False) -> list:
        """Lê uma página de um ``msgId`` já materializado.

        ``startRow`` além do fim devolve ``tableData`` vazio com HTTP 200 — é a
        condição de parada natural do laço, sem erro.
        """
        response = session.get(
            f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/result-paging",
            params={
                "nocache": int(time.time() * 1000), "startRow": start_row,
                "pageSize": self._VIP_PAGE_SIZE, "taskId": task_id, "msgId": msg_id,
                "isSetBenchMarkTime": "false", "benchMarkTimeRowNo": -1,
            }, timeout=120, allow_redirects=False,
        )
        if not self._check_session_valid(response, "trace"):
            raise SessionExpiredError("Sessão Trace expirada na paginação")
        response.raise_for_status()
        payload = response.json()
        if capture_raw:
            self._dump_raw(payload, "trace")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("tableData"), list):
            raise ValueError("result-paging retornou contrato inválido")
        return data["tableData"]

    @staticmethod
    def _trace_fields(message: dict) -> dict:
        return {field.get("name", ""): field.get("value", "")
                for field in (message.get("payload") or []) if isinstance(field, dict)}

    def _build_vip_measurements(self, task_id: int, vip_name: str, messages: list,
                                last_serial: int) -> tuple[list, int, int]:
        """Decodifica as linhas do filtro e devolve medições, cursor e não decodificadas.

        A resposta do FARS não vem ordenada por ``serialNo``, então a ordenação
        é feita aqui antes de avançar o cursor.

        Diferente de um decode remoto, ``decode_meas_report`` é função pura dos
        bytes: uma mensagem que falha hoje falha em todo ciclo futuro. Por isso o
        cursor avança sobre ela — parar ali travaria a coleta para sempre — mas a
        ocorrência é contada e volta como diagnóstico, nunca como zero.
        """
        rows, undecoded = [], 0
        safe_serial = last_serial
        ordered = sorted(messages, key=lambda item: int(item.get("serialNo") or -1))
        for message in ordered:
            try:
                serial = int(message["serialNo"])
            except (KeyError, TypeError, ValueError):
                undecoded += 1
                continue
            if serial <= last_serial:
                continue
            fields = self._trace_fields(message)
            if (fields.get("GULTrcMsgType") or message.get("msgType") or "") != "RRC_MEAS_RPRT":
                safe_serial = max(safe_serial, serial)
                continue
            decoded = decode_meas_report(message.get("messageBody"))
            if decoded is None:
                undecoded += 1
                safe_serial = max(safe_serial, serial)
                continue
            cell = str(fields.get("GLCellId") or "")
            source = message.get("source") or ""
            serving_cell = f"{source}_{cell}" if source and cell else (source or cell)
            rows.append({
                "vip_name": vip_name, "event_id": self.event_id, "task_id": task_id,
                "serial_no": serial, "timestamp": self._parse_trace_timestamp(fields.get("Time")),
                "serving_cell": serving_cell, "rsrp": decoded["rsrp"], "rsrq": decoded["rsrq"],
                "in_event": 1 if self._cell_in_event(serving_cell) else 0,
            })
            safe_serial = max(safe_serial, serial)
        return rows, safe_serial, undecoded

    @staticmethod
    def _log_vip_task(task_id, vip_name: str, record_count: int = 0, read: int = 0,
                      decoded: int = 0, reason: str = "") -> None:
        """Registra cada task de VIP, inclusive as que devolvem vazio ou falham cedo."""
        message = (
            f"[vip/{vip_name}] task {task_id}: recordCount={record_count} "
            f"linhas_lidas={read} linhas_decodificadas={decoded}"
        )
        if reason:
            logger.warning("%s motivo=%s", message, reason)
        else:
            logger.info(message)

    def collect_vips(self, mode: str = "incremental") -> CollectionResult:
        """Consome o Trace por ``filter-by-cols``, decodificando RSRP/RSRQ localmente."""
        from core.collection_result import CollectionDiagnostic

        self.vips_by_task = self._load_vips_by_task()
        if not self.vips_by_task:
            return CollectionResult.empty(
                "Nenhum VIP com task configurada para coleta de Trace.",
                coverage={"vips_configured": 0, "vips_with_data": 0, "tasks_configured": 0},
            )

        all_rows, cursors, diagnostics = [], {}, []
        task_details, failures = [], []
        renewed = False
        for attempt in range(2):
            all_rows, cursors, diagnostics, task_details, failures = [], {}, [], [], []
            try:
                session = self._get_session("trace")
                for raw_task_id, vip_name in self.vips_by_task.items():
                    try:
                        task_id = int(raw_task_id)
                    except (TypeError, ValueError):
                        reason = f"Task inválida para VIP {vip_name}: {raw_task_id}"
                        failures.append(("configuration", reason))
                        self._log_vip_task(raw_task_id, vip_name, reason=reason)
                        continue
                    checkpoints = db.get_collection_checkpoints(
                        self.event_id, "vip", task_id, self._trace_oss())
                    try:
                        # ``""`` é o object_key legado da assinatura por serial.
                        last_serial = int(checkpoints.get("serial") or checkpoints.get("") or 0)
                        start_row = int(checkpoints.get("row") or 0)
                    except (TypeError, ValueError):
                        reason = f"Checkpoint inválido da task {task_id}"
                        failures.append(("contract", reason))
                        self._log_vip_task(task_id, vip_name, reason=reason)
                        continue
                    try:
                        msg_id, win_start, win_end = self._open_trace_query(session, task_id)
                        if win_start is None or win_end is None:
                            task_details.append({
                                "task_id": task_id, "vip": vip_name, "msg_id": msg_id,
                                "serial_initial": last_serial, "serial_final": last_serial,
                                "messages": 0, "rrc_measurements": 0,
                                "decoded": 0, "undecoded": 0,
                                "row_initial": start_row, "row_final": start_row,
                                "record_count": 0, "backlog": False,
                            })
                            self._log_vip_task(task_id, vip_name)
                            continue
                        filtered_id, _ = self._filter_meas_reports(
                            session, task_id, msg_id, win_start, win_end)
                        sorted_id, total = self._sort_trace_by_time(
                            session, task_id, filtered_id)
                        # Uma task reiniciada recria o conjunto filtrado: índices e
                        # seriais voltam a zero e o cursor antigo passaria do fim,
                        # travando a coleta. Reler do início é seguro porque a chave
                        # única (task_id, serial_no) descarta o replay.
                        if start_row > total:
                            logger.warning(
                                f"[vip/{vip_name}] task {task_id}: conjunto filtrado encolheu "
                                f"({total} < offset {start_row}); relendo do início.")
                            last_serial, start_row = 0, 0
                        page = self._fetch_trace_page(
                            session, task_id, sorted_id, start_row, capture_raw=True
                        )
                        messages, row, pages = list(page), start_row + len(page), 1
                        while (len(page) == self._VIP_PAGE_SIZE
                               and pages < self._VIP_MAX_PAGES_PER_CYCLE):
                            page = self._fetch_trace_page(session, task_id, sorted_id, row)
                            messages.extend(page)
                            row += len(page)
                            pages += 1
                        rows, safe_serial, undecoded = self._build_vip_measurements(
                            task_id, vip_name, messages, last_serial)
                        backlog = row < total
                        # Marca d'água só avança sobre conjunto lido por inteiro. Num
                        # ciclo parcial ela descartaria as linhas de serial menor que
                        # ficaram para trás — o cursor `row` já garante a continuidade,
                        # e a chave única (task_id, serial_no) descarta o replay.
                        if backlog:
                            safe_serial = last_serial
                    except SessionExpiredError:
                        raise
                    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout) as error:
                        reason = f"Task {task_id} sem conexão: {error}"
                        failures.append(("network", reason))
                        self._log_vip_task(task_id, vip_name, reason=reason)
                        continue
                    except requests.exceptions.HTTPError as error:
                        reason = f"Task {task_id} retornou erro HTTP: {error}"
                        failures.append(("http", reason))
                        self._log_vip_task(task_id, vip_name, reason=reason)
                        continue
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                        reason = f"Task {task_id} inválida ou indisponível: {error}"
                        failures.append(("contract", reason))
                        self._log_vip_task(task_id, vip_name, reason=reason)
                        continue
                    all_rows.extend(rows)
                    if safe_serial > last_serial or row > start_row:
                        cursors[f"{task_id}:serial"] = {
                            "task_id": task_id, "object_key": "serial", "cursor": safe_serial}
                        cursors[f"{task_id}:row"] = {
                            "task_id": task_id, "object_key": "row", "cursor": row}
                    if undecoded:
                        logger.warning(
                            f"[vip/{vip_name}] task {task_id}: {undecoded} de {len(messages)} "
                            f"mensagens não decodificadas (formato não reconhecido).")
                        diagnostics.append(CollectionDiagnostic(
                            "decode", f"{undecoded} mensagens de {vip_name} não decodificadas",
                            "decode", {"task_id": task_id, "serial": safe_serial}))
                    if backlog:
                        diagnostics.append(CollectionDiagnostic(
                            "decode", f"Processando backlog de {vip_name}: {total - row} mensagens restantes",
                            "backlog", {"task_id": task_id, "serial": safe_serial}))
                    task_details.append({
                        "task_id": task_id, "vip": vip_name, "msg_id": sorted_id,
                        "serial_initial": last_serial, "serial_final": safe_serial,
                        "messages": len(messages), "rrc_measurements": len(messages),
                        "decoded": len(rows), "undecoded": undecoded,
                        "row_initial": start_row, "row_final": row, "record_count": total,
                        "backlog": backlog,
                    })
                    self._log_vip_task(
                        task_id, vip_name, record_count=total, read=len(messages), decoded=len(rows)
                    )
                break
            except SessionExpiredError:
                if renewed or attempt:
                    logger.error("Sessão de Trace continuou inválida após a renovação.")
                    return CollectionResult.auth_required("A sessão de Trace continuou inválida após a renovação.")
                if not self._renew_session("trace"):
                    logger.error("Não foi possível renovar a sessão de Trace.")
                    return CollectionResult.auth_required("Não foi possível renovar a sessão de Trace.")
                renewed = True
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as error:
                logger.error(f"Erro de conexão no Trace: {error}")
                return CollectionResult.error(f"Falha de conexão no Trace: {error}", code="network")
            except requests.exceptions.HTTPError as error:
                logger.error(f"HTTP no Trace: {error}")
                return CollectionResult.error(f"Falha HTTP no Trace: {error}", code="http")
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                logger.error(f"Contrato inválido do Trace: {error}")
                return CollectionResult.error(f"Contrato inválido do Trace: {error}", stage="parsing", code="contract")

        self._last_vip_subscription = task_details
        total_tasks = len(self.vips_by_task)
        vips_with_data = len({row["vip_name"] for row in all_rows})
        coverage = {
            "vips_configured": total_tasks, "vips_with_data": vips_with_data,
            "tasks_configured": total_tasks, "tasks_valid": len(task_details),
            "tasks_invalid": len(failures), "backlog_tasks": sum(1 for item in task_details if item["backlog"]),
            "backlog_messages": sum(max(0, item["record_count"] - item["row_final"])
                                    for item in task_details if item["backlog"]),
            "undecoded_messages": sum(item["undecoded"] for item in task_details),
            "latest_serial": max((item["serial_final"] for item in task_details), default=None),
        }
        for stage, message in failures:
            diagnostics.append(CollectionDiagnostic(stage, message, "task_invalid"))
        # Um ciclo sem log foi o que escondeu a falha anterior: registre sempre o
        # resultado, inclusive quando ele é zero.
        logger.info(
            f"VIPs: {len(all_rows)} medições de {vips_with_data}/{total_tasks} VIPs; "
            f"tasks ok={len(task_details)} falhas={len(failures)} "
            f"backlog={coverage['backlog_messages']} não decodificadas={coverage['undecoded_messages']} "
            f"serial={coverage['latest_serial']}")
        latest_data_at = max((row.get("timestamp") for row in all_rows), default=None)
        partial = bool(failures or any(item["backlog"] for item in task_details))
        if partial:
            return CollectionResult.partial(all_rows, cause="Trace processado parcialmente; confira tarefas ou backlog.",
                                            cursors=cursors, diagnostics=diagnostics, coverage=coverage,
                                            latest_data_at=latest_data_at)
        if all_rows:
            return CollectionResult.data(all_rows, cursors=cursors, diagnostics=diagnostics,
                                         coverage=coverage, latest_data_at=latest_data_at)
        return CollectionResult.empty("Trace respondeu sem mensagens novas de VIP.", cursors=cursors,
                                      diagnostics=diagnostics, coverage=coverage)

    def _handle_trace_error(self, task_id, step: str, resp: requests.Response):
        """Loga e alerta para erros HTTP do fluxo FARS."""
        body = resp.text or ""
        body_lower = body.lower()
        logger.error(
            f"HTTP {resp.status_code} em {step} taskId={task_id} body={body[:600]}"
        )
        if "linkage" in body_lower or "service running" in body_lower:
            try:
                db.insert_alert({
                    "event_id":  self.event_id,
                    "level":     "GLOBAL",
                    "severity":  "WARNING",
                    "site_id":   "GLOBAL",
                    "cell_id":   "",
                    "message":   f"Trace task {task_id} indisponível no iManager.",
                    "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                })
            except Exception:
                pass

    def _parse_trace_timestamp(self, raw) -> str:
        """
        Converte timestamp do FARS para UTC ISO com Z.
        iManager retorna horário local; aplica offset para converter para UTC.
        """
        if raw is None or raw == "":
            return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        s = str(raw)
        milliseconds = None
        match = re.search(r"\((\d{1,3})\)\s*$", s)
        if match:
            # O FARS não zero-preenche o campo: "(98)" são 98 ms, não 980.
            milliseconds = int(match.group(1).zfill(3))
            s = s[:match.start()].strip()
        # Tenta tratar como epoch ms (já é UTC)
        try:
            ms = int(s)
            return datetime.utcfromtimestamp(ms / 1000.0).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        except (ValueError, TypeError):
            pass
        # Timestamp local — converter para UTC
        try:
            dt_local = datetime.fromisoformat(s.replace(" ", "T"))
            # Offset negativo = atrás do UTC; subtrair para chegar em UTC
            # Ex: UTC-3 (offset=-180) → UTC = local_time - (-180 min) = local + 180 min
            from datetime import timedelta
            dt_utc = dt_local - timedelta(minutes=self._oss_tz_offset_min)
            suffix = f".{milliseconds:03d}" if milliseconds is not None else ""
            return dt_utc.strftime('%Y-%m-%dT%H:%M:%S') + suffix + 'Z'
        except Exception:
            return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    # ── Alarmes (iMaster FM website) ─────────────────────────────────
    def collect_alarms(self) -> CollectionResult:
        """Coleta alarmes correntes filtrados pelos tipos do evento (oss.alarm_filter).

        Reusa a sessão 'monitoring' (bspsession+roarand do session.json); em sessão
        expirada, renova 1× via a máquina existente (_renew_session) e re-tenta. Retorna
        linhas achatadas prontas para db.insert_alarms_batch (dedup por csn já aplicado)."""
        names = (self.event.get("oss", {}) or {}).get("alarm_filter") or _DEFAULT_ALARM_NAMES
        try:
            catalog = _load_alarm_catalog()
        except Exception as e:
            logger.error(f"Falha ao carregar o catálogo de alarmes: {e}")
            return CollectionResult.error(f"Falha ao carregar o catálogo de alarmes: {e}",
                                          stage="configuration", code="catalog")
        pairs, missing = _resolve_alarm_pairs(names, catalog)
        if missing:
            logger.warning(f"Tipos de alarme ignorados (ausentes no catálogo): {missing}")
        if not pairs:
            logger.warning("Filtro de alarmes vazio/inválido — coleta de alarmes ignorada.")
            return CollectionResult.empty("Nenhum tipo de alarme válido foi configurado.")
        condition = _build_alarm_condition(pairs)

        retries = 2
        renewed_this_call = False
        for attempt in range(retries):
            try:
                model_id = self._create_alarm_model(condition)
                raw = self._collect_alarm_pages(model_id)
                measurements = self._flatten_alarms(raw)
                if measurements:
                    return CollectionResult.data(measurements,
                                                 latest_data_at=max((m.get("arrive_time") for m in measurements), default=None))
                return CollectionResult.empty("Nenhum alarme novo retornado pelo iManager.")
            except SessionExpiredError as e:
                logger.warning(f"Erro de sessão no collect_alarms: {e}")
                if renewed_this_call:
                    self._engage_backoff("monitoring")
                    logger.error("Sessão monitoring renovada mas ainda inválida — "
                                 "pausando coleta de alarmes (backoff).")
                    return CollectionResult.auth_required(
                        "A sessão de alarmes continuou inválida após a renovação.")
                if attempt < retries - 1 and self._renew_session("monitoring"):
                    renewed_this_call = True
                    continue
                logger.error("Não foi possível renovar a sessão de alarmes.")
                return CollectionResult.auth_required("Não foi possível renovar a sessão de alarmes.")
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                logger.error(f"Erro de conexão ao coletar alarmes: {e}")
                return CollectionResult.error(f"Falha de conexão ao coletar alarmes: {e}", code="network")
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP ao coletar alarmes: {e}")
                return CollectionResult.error(f"Falha HTTP ao coletar alarmes: {e}", code="http")
            except (ValueError, TypeError, KeyError) as e:
                logger.error(f"Resposta inválida de alarmes: {e}")
                return CollectionResult.error(f"Resposta inválida de alarmes: {e}", stage="parsing", code="contract")
        return CollectionResult.error("Coleta de alarmes terminou sem resposta.", code="unknown")

    def _alarm_post(self, body: dict, cmd: int) -> dict:
        """POST no endpoint de comandos do FM website (sessão monitoring).

        Trata redirecionamento SSO/CSRF como sessão inválida (SessionExpiredError) e
        atualiza o roarand quando o servidor o rotaciona, como o standalone faz."""
        params = {"_t": int(time.time() * 1000), "_cmd": cmd}
        url = f"{self.base_url}{_ALARM_ENDPOINT}"
        s = self._get_session("monitoring")
        resp = s.post(url, params=params, json=body, timeout=30,
                      headers={"x-non-renewal-session": "true"}, allow_redirects=False)
        if not self._check_session_valid(resp, "monitoring"):
            raise SessionExpiredError(f"Sessão monitoring inválida no cmd {cmd} de alarmes")
        resp.raise_for_status()
        new_rand = resp.headers.get("roarand") or s.cookies.get("roarand")
        if new_rand:
            s.headers["roarand"] = new_rand
        return resp.json()

    def _create_alarm_model(self, condition: str) -> str:
        """cmd 1102 — cria a visão filtrada no servidor e devolve o modelID."""
        body = {
            "cmd": 1102,
            "parameters": {
                "modelID": None,
                "bspSessionId": "",
                "showStatistic": False,
                "additionalCondition": json.dumps(_ALARM_ADDITIONAL_CONDITION),
                "timeMode": 3,
                "urlCondition": None,
                "expression": "",
                "autoRefresh": False,   # foto estável
                "isScrollLock": False,
                "condition": condition,
            },
        }
        payload = self._alarm_post(body, 1102)
        model_id = _extract_alarm_model_id(payload)
        if not model_id:
            raise RuntimeError(
                "modelID não encontrado na resposta do cmd 1102. "
                f"Resposta: {json.dumps(payload)[:300]}"
            )
        return model_id

    def _fetch_alarm_page(self, model_id: str, frm: int, to: int) -> dict:
        """cmd 1103 — lê uma página do resultado já filtrado."""
        body = {
            "cmd": 1103,
            "parameters": {
                "modelID": model_id,
                "bspSessionId": "",
                "timeMode": 3,
                "versionFlag": False,
                "csns": [],
                "autoRefresh": False,   # foto estável para o bulk
                "scrollLock": False,
                "from": frm,
                "to": to,
            },
        }
        payload = self._alarm_post(body, 1103)
        return payload.get("parameters", payload)

    def _collect_alarm_pages(self, model_id: str) -> List[dict]:
        """Pagina o cmd 1103 até esgotar o total informado na primeira resposta."""
        first = self._fetch_alarm_page(model_id, 1, _ALARM_PAGE)
        total = int(first.get("total", 0))
        rows = list(first.get("data", []))
        frm = _ALARM_PAGE + 1
        while len(rows) < total:
            to = frm + _ALARM_PAGE - 1
            page = self._fetch_alarm_page(model_id, frm, to)
            batch = page.get("data", [])
            if not batch:
                break
            rows.extend(batch)
            frm += _ALARM_PAGE
            time.sleep(_ALARM_SLEEP)
        logger.info(f"Alarmes: {len(rows)}/{total} coletados (filtro por tipo).")
        return rows

    def _flatten_alarms(self, raw: List[dict]) -> List[dict]:
        """Achata + injeta event_id/collected_at e deduplica por csn (lista viva).

        arriveUtc/occurUtc chegam como "YYYY-MM-DD HH:MM:SS" NO FUSO DO CLIENTE
        (cookies timemode=client / timezone=America/Sao_Paulo), apesar do nome "Utc".
        Convertemos para UTC real (mesma lógica do trace, via _oss_tz_offset_min) para
        que o frontend — que interpreta o timestamp como UTC — exiba o horário certo."""
        collected_at = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        out = {}
        for a in raw:
            csn = a.get("csn")
            if csn is None:
                continue
            row = _flatten_alarm(a, self.event_id, collected_at)
            if row.get("arrive_time"):
                row["arrive_time"] = self._parse_trace_timestamp(row["arrive_time"])
            if row.get("occur_time"):
                row["occur_time"] = self._parse_trace_timestamp(row["occur_time"])
            out[csn] = row
        return list(out.values())


# ── Mock (desenvolvimento e testes) ──────────────────────────────────

class MockCollector(BaseCollector):
    """Gera dados sintéticos para desenvolvimento sem acesso ao OSS."""

    import random as _random

    def collect_kpis(self) -> CollectionResult:
        import random
        rows = []
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        sites = self.event.get("sites", [])

        for site in sites:
            for i, cell in enumerate(site.get("cells", [{"id": "Cell-A1"}])):
                cell_id = cell if isinstance(cell, str) else cell.get("id", f"Cell-{i}")
                for metric, base in [
                    ("utilization_dl", 50),
                    ("traffic_volume_dl", 120), ("traffic_volume_ul", 45),
                    ("throughput_dl", 40), ("throughput_ul", 15),
                    ("user_count", 30), ("accessibility", 99.5)
                ]:
                    value = max(0, base + random.gauss(0, 10 if metric != "accessibility" else 0.5))
                    if metric == "accessibility":
                        value = min(100.0, value)
                    rows.append({
                        "site_id":   site["id"],
                        "cell_id":   cell_id,
                        "event_id":  self.event_id,
                        "timestamp": now,
                        "metric":    metric,
                        "value":     round(value, 2),
                    })
        if rows:
            return CollectionResult.data(rows, latest_data_at=now)
        return CollectionResult.empty("O evento não possui células para gerar dados de demonstração.")

    def collect_vips(self, mode: str = "express") -> CollectionResult:
        import random
        rows = []
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        all_cells = [
            c if isinstance(c, str) else c.get("id", "")
            for s in self.event.get("sites", [])
            for c in s.get("cells", [{"id": f"{s['id']}-A"}])
        ]

        event_vips = db.get_event_vips(self.event_id)
        for vip in event_vips:
            serving = random.choice(all_cells) if all_cells else ""
            in_event = self._cell_in_event(serving)
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
        coverage = {"vips_configured": len(event_vips), "vips_with_data": len(rows)}
        if rows:
            return CollectionResult.data(rows, coverage=coverage, latest_data_at=now)
        return CollectionResult.empty("Nenhum VIP configurado para demonstração.", coverage=coverage)

    def collect_alarms(self) -> CollectionResult:
        """Alarmes sintéticos (Cell Unavailable / VSWR) para dev em --mock/navegador."""
        from datetime import timedelta
        now = datetime.utcnow()
        collected_at = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        base_csn = int(now.timestamp())
        types = [
            ("Cell Unavailable", "Major", "3600", "268435456"),
            ("RF Unit VSWR Threshold Crossed", "Critical", "26529", "268435456"),
        ]
        sites = self.event.get("sites", []) or [{"id": "DEMO", "name": "Site Demo"}]
        rows = []
        for i, site in enumerate(sites[:6]):
            name, sev, aid, gid = types[i % len(types)]
            ts = (now - timedelta(minutes=i * 3)).strftime('%Y-%m-%dT%H:%M:%SZ')
            rows.append({
                "csn":             base_csn + i,
                "event_id":        self.event_id,
                "alarm_id":        aid,
                "alarm_group_id":  gid,
                "alarm_name":      name,
                "severity":        sev,
                "source":          f"SR-{site['id']}",
                "ip":              f"10.0.0.{10 + i}",
                "location":        site.get("name", ""),
                "occur_time":      ts,
                "arrive_time":     ts,
                "additional_info": "mock",
                "collected_at":    collected_at,
            })
        return CollectionResult.data(rows, latest_data_at=collected_at)


# ── NullCollector (produção sem fonte configurada) ───────────────────

class NullCollector(BaseCollector):
    """Reporta ausência de fonte sem fingir uma coleta saudável com dados."""
    def collect_kpis(self) -> CollectionResult:
        return CollectionResult.empty("Nenhuma fonte de KPI foi configurada.")

    def collect_vips(self, mode: str = "express") -> CollectionResult:
        return CollectionResult.empty("Nenhuma fonte de VIP foi configurada.")

    def collect_alarms(self) -> CollectionResult:
        return CollectionResult.empty("Nenhuma fonte de alarmes foi configurada.")


# ── Factory ──────────────────────────────────────────────────────────

def build_collector(event_config: dict, mock: bool = False) -> BaseCollector:
    if mock:
        return MockCollector(event_config)

    oss = event_config.get("oss", {})
    import_folder = oss.get("import_folder", "")

    if import_folder and Path(import_folder).exists():
        return CsvCollector(event_config, import_folder)

    # Resolve a base_url pelo catálogo Cliente→Regional (oss.base_url tem precedência).
    base_url = credentials.resolve_base_url(oss)

    return HttpCollector(event_config, base_url)
