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

    @abstractmethod
    def collect_kpis(self) -> List[dict]:
        """Retorna lista de medições de KPI para inserção no banco."""
        ...

    @abstractmethod
    def collect_vips(self, mode: str = "express") -> List[dict]:
        """Retorna última leitura de RSRP/RSRQ de cada VIP."""
        ...

    @abstractmethod
    def collect_alarms(self) -> List[dict]:
        """Retorna alarmes correntes filtrados por tipo para inserção no banco."""
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

    def collect_vips(self, mode: str = "express") -> List[dict]:
        # CSV de trace de VIPs foi descontinuado: o esquema atual identifica
        # cada VIP pelo task_id da sua task dedicada no iManager (via HTTP).
        # Se você ainda precisa importar trace de CSV, ele teria que carregar
        # uma coluna explícita com o nome do VIP.
        return []

    def collect_alarms(self) -> List[dict]:
        # Coleta de alarmes por CSV está fora de escopo (só via HTTP no iManager).
        return []

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

    # Backoff de renovação de sessão — persiste entre instâncias (collector recriado ao
    # trocar de evento).  Chave = nome do módulo ("trace" | "monitoring").
    _renew_failures: dict = {}        # módulo → nº de falhas consecutivas
    _renew_backoff_until: dict = {}   # módulo → datetime até quando não tentar
    # Quando a renovação headless detecta CAPTCHA/SSO (exit 2), o módulo entra em
    # "requer reautenticação interativa": paramos de tentar renovar sozinhos e
    # mantemos um alerta acionável até o operador reautenticar (navegador visível).
    # Valor = snapshot do roarand no momento em que foi marcado (para detectar a
    # reauth do operador comparando com o roarand atual do session.json).
    _needs_interactive: dict = {}     # módulo → roarand snapshot (str) | ""
    # Reautenticação interativa (navegador visível): trava single-flight para nunca
    # abrir duas janelas ao mesmo tempo (threads monitoring + trace) e cooldown para
    # não reabrir em rajada após uma tentativa cancelada/falha pelo operador.
    _interactive_lock = threading.Lock()
    _interactive_cooldown_until = None  # datetime | None

    # Limites de decodificação de mensagens (RRC_MEAS_RPRT) de VIPs
    _VIP_DECODES_EXPRESS = 3
    _VIP_DECODES_FULL = 20
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
        self.session_cookie = session_cookie
        self._session_file = self._resolve_session_file(self.base_url)
        self._session_monitoring = None
        self._session_trace = None
        # roarand com que cada sessão em cache foi construída — usado para detectar
        # quando o session.json foi renovado externamente (outra thread / reauth do
        # operador) e a sessão em cache ficou obsoleta.
        self._session_built_roarand: dict = {}
        self._renew_lock = threading.Lock()
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
        for site in event_config.get("sites", []):
            for cell in site.get("cells", []):
                c_id = cell if isinstance(cell, str) else cell.get("id")
                if c_id:
                    self._cell_to_site[c_id] = site["id"]
                    if isinstance(cell, dict) and "obj_no" in cell:
                        obj_no = int(cell["obj_no"])
                        self._static_obj_nos.add(obj_no)
                        self._obj_to_cell[obj_no] = {
                            "cell_id": c_id,
                            "site_id": site["id"]
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

    def _clear_interactive_state(self, module: str):
        HttpCollector._needs_interactive.pop(module, None)

    @classmethod
    def reset_interactive_state(cls, module: Optional[str] = None):
        """Limpa o estado 'requer reauth interativa' (chamado após uma reauth bem-sucedida)."""
        if module is None:
            cls._needs_interactive.clear()
            cls._renew_failures.clear()
            cls._renew_backoff_until.clear()
            cls._interactive_cooldown_until = None
        else:
            cls._needs_interactive.pop(module, None)
            cls._renew_failures.pop(module, None)
            cls._renew_backoff_until.pop(module, None)

    @staticmethod
    def _build_renew_cmd() -> list:
        """Comando-base para rodar o renovador (subprocesso). No .exe usa `--get-session`;
        em dev usa o Python do venv + scratch/get_session.py."""
        root = Path(__file__).parent.parent
        if getattr(sys, "frozen", False):
            return [sys.executable, "--get-session"]
        python_exe = root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = Path("python")
        return [str(python_exe), str(root / "scratch" / "get_session.py")]

    @classmethod
    def run_interactive_reauth(cls, base_url: str, session_file, region: str = "",
                              respect_cooldown: bool = False, cliente: str = "") -> dict:
        """Abre o navegador VISÍVEL (single-flight) para o operador concluir o login +
        CAPTCHA, captura a sessão e a grava. Retorna {'ok':bool,...}.

        - single-flight: nunca abre duas janelas simultâneas (monitoring + trace).
        - respect_cooldown=True (auto-open): pula se houve falha/cancelamento recente.
        - cliente/region: escolhem as credenciais (Cliente → Regional) para o autofill do login.
        """
        from datetime import timedelta
        if respect_cooldown:
            cd = cls._interactive_cooldown_until
            if cd and datetime.utcnow() < cd:
                return {"ok": False, "error": "cooldown", "skipped": True}
        if not cls._interactive_lock.acquire(blocking=False):
            return {"ok": False, "error": "Reautenticação já em andamento.", "in_progress": True}
        try:
            base_url = (base_url or "").rstrip("/")
            cmd = cls._build_renew_cmd() + [
                "--module", "both",            # sem --headless → navegador visível
                "--base-url", base_url,
                "--session-file", str(session_file),
                "--region", region or "",
                "--cliente", cliente or "",
            ]
            logger.info(f"[reauth] Abrindo navegador visível para reautenticação ({base_url})...")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=320,
                cwd=str(Path(__file__).parent.parent),
            )
            if result.returncode == EXIT_SUCCESS:
                cls.reset_interactive_state()
                logger.info("[reauth] Reautenticação interativa concluída com sucesso. Coleta retomada.")
                return {"ok": True, "base_url": base_url}
            tail = ((result.stdout or "")[-400:] + " " + (result.stderr or "")[-400:]).strip()
            cls._interactive_cooldown_until = datetime.utcnow() + timedelta(minutes=5)
            logger.error(f"[reauth] Reautenticação não concluída (rc={result.returncode}): {tail}")
            return {
                "ok": False,
                "error": "Reautenticação não concluída. Confira usuário/senha e o código (CAPTCHA).",
                "detail": tail,
            }
        except subprocess.TimeoutExpired:
            cls._interactive_cooldown_until = datetime.utcnow() + timedelta(minutes=5)
            return {"ok": False, "error": "Tempo limite de reautenticação excedido (5 min)."}
        except Exception as e:
            cls._interactive_cooldown_until = datetime.utcnow() + timedelta(minutes=5)
            logger.error(f"[reauth] erro ao executar reautenticação: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            cls._interactive_lock.release()

    def _spawn_interactive_reauth(self):
        """Dispara a reauth interativa em uma thread separada (não bloqueia a coleta).
        Abre o navegador automaticamente quando o CAPTCHA é detectado; o single-flight
        e o cooldown garantem que não abra janelas em excesso."""
        if HttpCollector._interactive_lock.locked():
            return
        cd = HttpCollector._interactive_cooldown_until
        if cd and datetime.utcnow() < cd:
            return
        base_url, session_file, region = self.base_url, self._session_file, self._region
        cliente = self._cliente

        def _worker():
            res = HttpCollector.run_interactive_reauth(
                base_url, session_file, region=region, respect_cooldown=True, cliente=cliente)
            if res.get("ok"):
                # Descarta as sessões em cache desta instância para releitura imediata.
                self._invalidate_session("monitoring")
                self._invalidate_session("trace")

        threading.Thread(target=_worker, name="interactive-reauth", daemon=True).start()

    def _engage_backoff(self, module: str) -> int:
        """Incrementa o contador de falhas e arma o backoff (60→300s). Retorna o backoff em s."""
        from datetime import timedelta
        failures = HttpCollector._renew_failures.get(module, 0) + 1
        HttpCollector._renew_failures[module] = failures
        backoff_s = min(300, 60 * failures)
        HttpCollector._renew_backoff_until[module] = datetime.utcnow() + timedelta(seconds=backoff_s)
        return backoff_s

    def _oss_region_label(self) -> str:
        oss = self.event.get("oss", {}) if isinstance(self.event, dict) else {}
        region = (oss.get("region") or "").upper()
        if region:
            return region
        try:
            import urllib.parse
            return urllib.parse.urlparse(self.base_url).hostname or self.base_url
        except Exception:
            return self.base_url

    def _raise_reauth_alert(self, module: str):
        """Emite UM alerta persistente e acionável pedindo reautenticação manual.
        O alerta é por-regional (login/sessão é compartilhado entre os módulos), então
        trace e monitoring não geram alertas duplicados."""
        region = self._oss_region_label()
        msg = (f"Sessão do iManager ({region}) bloqueada por CAPTCHA/SSO — "
               f"reautenticação manual necessária. Clique para reautenticar.")
        try:
            active = db.get_active_alerts(self.event_id)
            if not any(a.get("message") == msg for a in active):
                db.insert_alert({
                    "event_id":  self.event_id,
                    "level":     "GLOBAL",
                    "severity":  "CRITICAL",
                    "site_id":   "GLOBAL",
                    "cell_id":   "",
                    "message":   msg,
                    "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                })
        except Exception as e:
            logger.warning(f"Não foi possível inserir alerta de reauth: {e}")

    def _renew_session(self, module: str) -> bool:
        """
        Renova a sessão via Playwright (subprocesso headless). Retorna True somente
        quando a renovação foi EFETIVA (sessão autenticada gravada); False caso
        contrário (backoff ativo, CAPTCHA/SSO exigindo reauth, ou falha).

        - Backoff exponencial (60→300s) após falhas consecutivas, evitando loop.
        - exit 2 (EXIT_NEEDS_INTERACTIVE): login bloqueado por CAPTCHA/SSO. Marca o módulo
          como 'requer reauth interativa', emite UM alerta acionável e para de tentar
          renovar sozinho até o operador reautenticar (api.reauth_session) — isto elimina
          o loop infinito de renovações que nunca autenticavam.
        - Detecção de reauth do operador / de renovação por outra thread: se o roarand do
          session.json mudou, recarrega a sessão sem rodar Playwright.
        """
        # ── Já aguardando reauth interativa? Só sai disso quando o operador reautentica ──
        if module in HttpCollector._needs_interactive:
            roarand_marked = HttpCollector._needs_interactive.get(module) or ""
            module_data = self._load_session_data().get(module, {}) or {}
            roarand_now = module_data.get("roarand") or ""
            if roarand_now and roarand_now != roarand_marked and module_data.get("cookies"):
                logger.info(
                    f"[renew/{module}] Reautenticação interativa detectada (roarand mudou). "
                    "Recarregando sessão e retomando a coleta."
                )
                HttpCollector._renew_failures.pop(module, None)
                HttpCollector._renew_backoff_until.pop(module, None)
                self._clear_interactive_state(module)
                self._invalidate_session(module)
                return True
            # Continua bloqueado: mantém o alerta acionável e reabre o navegador
            # automaticamente (single-flight + cooldown), sem rodar Playwright headless.
            self._raise_reauth_alert(module)
            self._spawn_interactive_reauth()
            return False

        # ── Backoff: se falhou recentemente, pula esta tentativa ─────────
        now = datetime.utcnow()
        backoff_until = HttpCollector._renew_backoff_until.get(module)
        if backoff_until and now < backoff_until:
            remaining = int((backoff_until - now).total_seconds())
            logger.warning(
                f"[renew/{module}] Backoff ativo — pulando renovação por mais {remaining}s "
                f"({HttpCollector._renew_failures.get(module, 0)} falha(s) consecutiva(s))."
            )
            return False

        # ── Sessão em cache obsoleta? Se o session.json já tem um roarand diferente do
        #    que a sessão em cache usou, alguém renovou (outra thread OU a reauth interativa
        #    do operador): basta recarregar do arquivo, sem rodar Playwright (evita reabrir
        #    o navegador logo após um login bem-sucedido). ──────────────────────────────
        _mod_data = self._load_session_data().get(module, {}) or {}
        _file_roarand = _mod_data.get("roarand")
        if (_file_roarand and _file_roarand != self._session_built_roarand.get(module)
                and _mod_data.get("cookies")):
            logger.info(
                f"[renew/{module}] session.json mais novo que a sessão em cache "
                "(renovado externamente) — recarregando sem Playwright."
            )
            self._invalidate_session(module)
            return True

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
                    logger.info(
                        f"[renew/{module}] Sessão já renovada por outra thread. "
                        "Recarregando sessão local sem rodar Playwright."
                    )
                    self._invalidate_session(module)
                    return True
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

            logger.info(f"[renew/{module}] Iniciando renovação de sessão via Playwright (subprocesso, timeout=120s)")
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
                    logger.info(f"[renew/{module}] Sessão renovada e autenticada com sucesso.")
                    if stdout_tail:
                        logger.debug(f"[renew/{module}] stdout:\n{stdout_tail}")
                    HttpCollector._renew_failures[module] = 0
                    HttpCollector._renew_backoff_until.pop(module, None)
                    self._clear_interactive_state(module)
                    self._invalidate_session(module)
                    return True

                if result.returncode == EXIT_NEEDS_INTERACTIVE:
                    # CAPTCHA/SSO: renovação headless é impossível. Marca o módulo, emite
                    # alerta acionável e ABRE o navegador visível automaticamente para o
                    # operador logar (single-flight evita janela duplicada monitoring+trace).
                    snapshot = (self._load_session_data().get(module, {}) or {}).get("roarand") or ""
                    HttpCollector._needs_interactive[module] = snapshot
                    logger.error(
                        f"[renew/{module}] Login bloqueado por CAPTCHA/SSO — abrindo navegador "
                        f"para reautenticação interativa. Coleta deste módulo pausada até logar.\n"
                        f"  STDOUT: {stdout_tail or '(vazio)'}\n  STDERR: {stderr_tail or '(vazio)'}"
                    )
                    self._raise_reauth_alert(module)
                    self._spawn_interactive_reauth()
                    return False

                # Falha genérica (EXIT_GENERIC_FAIL ou returncode inesperado)
                backoff_s = self._engage_backoff(module)
                failures = HttpCollector._renew_failures.get(module, 0)
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
                failures = HttpCollector._renew_failures.get(module, 0)
                logger.error(
                    f"[renew/{module}] Renovação TIMEOUT após 120s "
                    f"(tentativa #{failures}). Próxima tentativa em {backoff_s}s."
                )
                return False
            except Exception as e:
                backoff_s = self._engage_backoff(module)
                failures = HttpCollector._renew_failures.get(module, 0)
                logger.error(
                    f"[renew/{module}] Erro ao executar a renovação "
                    f"(tentativa #{failures}): {e}. Próxima tentativa em {backoff_s}s."
                )
                return False

    def collect_kpis(self) -> List[dict]:
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
                return self._parse_kpi_response(data)
            except SessionExpiredError as e:
                logger.warning(f"Erro de sessão no collect_kpis: {e}")
                if renewed_this_call:
                    # Já renovamos nesta chamada e a sessão renovada AINDA é inválida.
                    # Não renovar de novo (evita loop): engata backoff e para por aqui.
                    self._engage_backoff("monitoring")
                    logger.error("Sessão monitoring renovada mas ainda inválida — pausando coleta de KPIs (backoff).")
                    return []
                if attempt < retries - 1:
                    logger.warning("Sessão do módulo monitoring expirada. Iniciando renovação automática...")
                    if self._renew_session("monitoring"):
                        renewed_this_call = True
                        continue
                    # Renovação não efetiva (backoff/CAPTCHA): não adianta retentar agora.
                    return []
                return []
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
                return []
            except Exception as e:
                logger.error(f"Erro ao consultar KPIs: {e}")
                return []
        return []

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

    # Limite de mensagens RRC_MEAS_RPRT que tentamos decodificar por ciclo
    # (cada uma exige uma chamada extra ao msg-explain-info).
    _MAX_MEAS_DECODES_PER_CYCLE = 50
    _TRACE_PAGE_SIZE = 500

    def _collect_one_vip(self, task_id: int, vip_name: str, mode: str) -> List[dict]:
        """
        Executa o fluxo de coleta completo de 4 etapas para um único VIP.
        Roda em uma thread do ThreadPoolExecutor e usa uma requests.Session exclusiva.
        """
        s = self._build_session("trace")
        try:
            # 1) pre-check: inicializa a sessão de query server-side
            pre_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/pre-check"
            pre_resp = s.get(pre_url, params={
                "taskId": task_id,
                "queryType": 0,
                "nocache": int(time.time() * 1000),
            }, timeout=30)
            if not self._check_session_valid(pre_resp, "trace"):
                raise SessionExpiredError("Sessão trace expirada no worker (pre-check)")
            if pre_resp.status_code >= 400:
                self._handle_trace_error(task_id, "pre-check", pre_resp)
                return []
            pre_data = pre_resp.json() if pre_resp.content else {}
            if not pre_data.get("checkState", False):
                logger.warning(f"pre-check falhou para task {task_id}: {pre_data}")
                return []

            # 2) query/result para obter sess_msg_id
            result_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/result"
            result_resp = s.get(result_url, params={
                "nocache": int(time.time() * 1000),
                "startRow": 0,
                "pageSize": 10,
                "taskId": task_id,
                "msgId": 1,
                "isSetBenchMarkTime": "false",
                "benchMarkTimeRowNo": -1,
            }, timeout=30)
            if not self._check_session_valid(result_resp, "trace"):
                raise SessionExpiredError("Sessão trace expirada no worker (query/result)")
            if result_resp.status_code >= 400:
                self._handle_trace_error(task_id, "query/result", result_resp)
                return []

            result_data = result_resp.json() if result_resp.content else {}
            data_block = result_data.get("data") or {}
            if isinstance(data_block, list):
                logger.warning(f"query/result retornou lista para task {task_id} — sem sess_msg_id")
                return []
            sess_msg_id = data_block.get("msgId")
            if not sess_msg_id:
                logger.warning(f"Sem sess_msg_id para task {task_id}: {data_block}")
                return []

            # 3) filter-by-cols — RRC_MEAS_RPRT, mais recente primeiro
            filter_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols"
            _now_local = datetime.utcnow()
            _filter_end   = _now_local.strftime('%Y-%m-%d %H:%M:%S')
            _filter_start = "2000-01-01 00:00:00"
            filter_payload = {
                "colFilterDto": {
                    "colFltExpSeq": [{
                        "fieldId": "Message Type",
                        "value": "RRC_MEAS_RPRT",
                        "operator": {"op": 0}
                    }],
                    "signalList": [],
                    "hasStartTime": False,
                    "startTime": _filter_start,
                    "hasEndTime": False,
                    "endTime": _filter_end,
                    "isReverse": False
                },
                "pageDto": {
                    "sqlColumnName": "Time",
                    "isAscend": False,  # mais recente primeiro
                    "taskId": task_id,
                    "msgId": sess_msg_id,
                    "comparisonMsgId": -1,
                    "startRow": 0,
                    "pageSize": 1000,
                    "templateName": [],
                    "isSetBenchMarkTime": False,
                    "benchMarkTimeRowNo": -1
                }
            }
            # POST sem ?nocache= (limpeza; ver collect_kpis). allow_redirects=False para
            # detectar 302→SSO como sessão inválida em vez de seguir o redirect → 404 enganoso.
            filter_resp = s.post(
                filter_url,
                json=filter_payload,
                timeout=60,
                allow_redirects=False,
            )
            if not self._check_session_valid(filter_resp, "trace"):
                raise SessionExpiredError("Sessão trace expirada no worker (filter-by-cols)")
            if filter_resp.status_code >= 400:
                self._handle_trace_error(task_id, "filter-by-cols", filter_resp)
                return []

            # 4) parsing + msg-explain-info para RSRP/RSRQ
            max_decodes = self._VIP_DECODES_EXPRESS if mode == "express" else self._VIP_DECODES_FULL
            return self._parse_filtered_trace_response(
                filter_resp.json(), task_id, s, vip_name, sess_msg_id, max_decodes
            )

        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.error(f"Erro de conexão ao consultar VIP {vip_name} (taskId={task_id}): {e}")
            try:
                active_alerts = db.get_active_alerts(self.event_id)
                conn_msg = f"Falha de conexão com o iManager (Trace VIP {vip_name}). Verifique a VPN."
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
                logger.warning(f"Não foi possível inserir alerta de conexão VIP: {alert_ex}")
            return []
        except Exception as e:
            if not isinstance(e, SessionExpiredError):
                logger.error(f"Erro ao consultar VIP {vip_name} (taskId={task_id}): {e}")
            raise

    def collect_vips(self, mode: str = "express") -> List[dict]:
        """
        Coleta resultados de trace via fluxo paralelo com ThreadPoolExecutor.
        Antes da execução paralela, faz um preflight sequencial para garantir e/ou renovar a sessão.
        """
        import time
        start_time = time.time()

        self.vips_by_task = self._load_vips_by_task()
        if not self.vips_by_task:
            logger.warning(
                "Nenhum VIP com task_id configurado — sem coleta de trace. "
                "Defina 'task_id' em cada VIP do evento."
            )
            return []

        # 1) Preflight sequencial: garante que a sessão 'trace' está ativa.
        # Tenta fazer pre-check com o primeiro task_id do pool.
        first_task_id = list(self.vips_by_task.keys())[0]
        preflight_ok = False
        retries = 2
        renewed_this_call = False
        
        for attempt in range(retries):
            s = self._get_session("trace")
            try:
                pre_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/pre-check"
                pre_resp = s.get(pre_url, params={
                    "taskId": first_task_id,
                    "queryType": 0,
                    "nocache": int(time.time() * 1000),
                }, timeout=30)
                if not self._check_session_valid(pre_resp, "trace"):
                    raise SessionExpiredError("Sessão trace expirada no preflight")
                preflight_ok = True
                break
            except SessionExpiredError as e:
                logger.warning(f"Sessão expirada no preflight de trace: {e}")
                if renewed_this_call:
                    self._engage_backoff("trace")
                    logger.error("Sessão trace renovada no preflight mas continua inválida (backoff).")
                    break
                if attempt < retries - 1:
                    logger.warning("Iniciando renovação automática da sessão de trace no preflight...")
                    if self._renew_session("trace"):
                        renewed_this_call = True
                        continue
                    break
                break
            except Exception as e:
                logger.warning(f"Erro inesperado no preflight de trace: {e}")
                preflight_ok = True
                break
                
        if not preflight_ok:
            logger.error("Preflight de trace falhou. Abortando ciclo de VIPs.")
            return []

        # 2) Execução paralela com ThreadPoolExecutor
        import concurrent.futures
        measurements = []
        workers = self._VIP_MAX_WORKERS
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._collect_one_vip, task_id, vip_name, mode): (task_id, vip_name)
                for task_id, vip_name in self.vips_by_task.items()
            }
            
            for future in concurrent.futures.as_completed(futures):
                task_id, vip_name = futures[future]
                try:
                    res = future.result()
                    if res:
                        measurements.extend(res)
                except SessionExpiredError as e:
                    logger.warning(f"Worker reportou expiração de sessão para VIP {vip_name}: {e}")
                except Exception:
                    pass

        # Calcular contadores de log-resumo
        elapsed = time.time() - start_time
        total_vips = len(self.vips_by_task)
        
        vips_with_data = set()
        total_meas = len(measurements)
        in_event_count = 0
        for m in measurements:
            vips_with_data.add(m["vip_name"])
            if m.get("in_event"):
                in_event_count += 1
                
        num_vips_with_data = len(vips_with_data)
        
        logger.info(
            f"Trace/VIP (modo={mode}): {num_vips_with_data}/{total_vips} VIPs com dados, "
            f"{total_meas} medições, {in_event_count} no evento, em {elapsed:.2f}s."
        )
                    
        return measurements

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

    def _parse_trace_response(self, response_json: dict, task_id, session,
                               vip_name: str) -> List[dict]:
        """
        Parse do schema retornado por /traceresult/query/result.

        Como cada task é dedicada a um VIP, todas as mensagens RRC_MEAS_RPRT
        encontradas pertencem ao vip_name informado.

        Formato esperado da resposta:
          {
            "recordCount": int,
            "data": {
              "msgId": int,            # ID interno da sessão de query
              "tableData": [
                {
                  "serialNo": int,
                  "source":   "SR-...",
                  "messageBody": "04 4B ...",
                  "payload": [{"name": "...", "value": "...", "children": [...]}, ...]
                },
                ...
              ]
            }
          }

        Para RRC_MEAS_RPRT, RSRP/RSRQ NÃO ficam neste payload — é preciso
        chamar /traceresult/query/msg-explain-info com (msgId da sessão, rowNo).
        """
        rows = []
        data = response_json.get("data") or {}
        if isinstance(data, list):
            table = data
            sess_msg_id = None
        else:
            table = data.get("tableData") or data.get("result") or []
            sess_msg_id = data.get("msgId")

        if not table:
            return rows

        decodes_used = 0
        for idx, item in enumerate(table):
            fields = {f.get("name", ""): f.get("value", "")
                      for f in (item.get("payload") or [])}

            msg_type = (
                fields.get("GULTrcMsgType")
                or item.get("msgType")
                or ""
            )
            if msg_type != "RRC_MEAS_RPRT":
                continue
            if decodes_used >= self._MAX_MEAS_DECODES_PER_CYCLE:
                break
            if sess_msg_id is None:
                continue

            decodes_used += 1
            content_json = self._fetch_msg_explain_info(
                session, task_id, sess_msg_id, idx + 1  # FARS usa rowNo 1-indexado
            )
            if content_json is None:
                continue

            rsrp, rsrq = self._extract_rsrp_rsrq_from_json(content_json)
            if rsrp is None:
                continue

            source = item.get("source") or ""
            cell_id = str(fields.get("GLCellId") or "")
            if source:
                serving_cell_value = f"{source}_{cell_id}" if cell_id else source
            else:
                serving_cell_value = cell_id

            rows.append({
                "vip_name":    vip_name,
                "event_id":    self.event_id,
                "timestamp":   self._parse_trace_timestamp(fields.get("Time")),
                "serving_cell": serving_cell_value,
                "rsrp":        rsrp,
                "rsrq":        rsrq,
                "in_event":    1 if self._cell_in_event(serving_cell_value) else 0,
            })

        return rows

    def _parse_filtered_trace_response(self, response_json: dict, task_id, session,
                                       vip_name: str, sess_msg_id: int, max_decodes: int = None) -> List[dict]:
        """
        Parse do schema retornado por /traceresult/query/filter-by-cols.
        Como o filter-by-cols já filtrou por Message Type = RRC_MEAS_RPRT,
        não precisamos checar o tipo de mensagem no loop. A ordenação é decrescente
        por Time (mais recente primeiro), logo os primeiros max_decodes
        registros serão os mais recentes.
        """
        rows = []
        data = response_json.get("data") or {}
        if isinstance(data, list):
            table = data
        else:
            table = data.get("tableData") or data.get("result") or []

        if not table:
            return rows

        if max_decodes is None:
            max_decodes = self._MAX_MEAS_DECODES_PER_CYCLE

        decodes_used = 0
        for idx, item in enumerate(table):
            if decodes_used >= max_decodes:
                break
            if sess_msg_id is None:
                continue

            decodes_used += 1
            row_no = idx + 1  # FARS usa rowNo 1-indexado da página atual
            content_json = self._fetch_msg_explain_info(
                session, task_id, sess_msg_id, row_no
            )
            if content_json is None:
                continue

            rsrp, rsrq = self._extract_rsrp_rsrq_from_json(content_json)
            if rsrp is None:
                continue

            fields = {f.get("name", ""): f.get("value", "")
                      for f in (item.get("payload") or [])}

            source = item.get("source") or ""
            cell_id = str(fields.get("GLCellId") or "")
            if source:
                serving_cell_value = f"{source}_{cell_id}" if cell_id else source
            else:
                serving_cell_value = cell_id

            rows.append({
                "vip_name":    vip_name,
                "event_id":    self.event_id,
                "timestamp":   self._parse_trace_timestamp(fields.get("Time")),
                "serving_cell": serving_cell_value,
                "rsrp":        rsrp,
                "rsrq":        rsrq,
                "in_event":    1 if self._cell_in_event(serving_cell_value) else 0,
            })

        return rows

    def _parse_trace_timestamp(self, raw) -> str:
        """
        Converte timestamp do FARS para UTC ISO com Z.
        iManager retorna horário local; aplica offset para converter para UTC.
        """
        if raw is None or raw == "":
            return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        s = str(raw)
        if " (" in s:
            s = s.split(" (")[0]
        # Tenta tratar como epoch ms (já é UTC)
        try:
            ms = int(s)
            return datetime.utcfromtimestamp(ms / 1000.0).strftime('%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, TypeError):
            pass
        # Timestamp local — converter para UTC
        try:
            dt_local = datetime.fromisoformat(s.replace(" ", "T"))
            # Offset negativo = atrás do UTC; subtrair para chegar em UTC
            # Ex: UTC-3 (offset=-180) → UTC = local_time - (-180 min) = local + 180 min
            from datetime import timedelta
            dt_utc = dt_local - timedelta(minutes=self._oss_tz_offset_min)
            return dt_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        except Exception:
            return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    def _fetch_msg_explain_info(self, session, task_id, sess_msg_id, row_no):
        """
        Chama /traceresult/query/msg-explain-info para decodificar uma mensagem.

        Atenção: rowNo aqui é 1-indexado e refere-se à posição da linha na
        página de query/result — NÃO é o serialNo da mensagem.
        """
        url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info"
        try:
            resp = session.get(url, params={
                "nocache": int(time.time() * 1000),
                "taskId": task_id,
                "msgId": sess_msg_id,
                "rowNo": row_no,
                "tabularFlag": "y",
                "isSubscribe": "false",
                "isSecondDecode": "false",
                "isPlayback": "false",
            }, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.debug(
                f"msg-explain-info HTTP {resp.status_code} (rowNo={row_no}): {resp.text[:200]}"
            )
        except Exception as e:
            logger.debug(f"msg-explain-info exception (rowNo={row_no}): {e}")
        return None

    def _extract_rsrp_rsrq_from_json(self, content) -> tuple:
        """
        Procura recursivamente nós cujo `name` é rsrpResult/rsrqResult.

        Formato esperado no node: `{"name": "rsrpResult", "val": ": ---- 0x35(53) ---- *0110101"}`
        Mapeamento LTE: RSRP = index − 140 dBm,  RSRQ = index/2 − 19.5 dB.
        """
        rsrp = rsrq = None

        def parse_index(val_str: str):
            try:
                return int(val_str.split("(")[1].split(")")[0])
            except (ValueError, IndexError):
                return None

        def visit(node):
            nonlocal rsrp, rsrq
            if isinstance(node, dict):
                name = node.get("name", "")
                val = node.get("val") or node.get("value") or ""
                if rsrp is None and "rsrpResult" in name and "0x" in str(val):
                    idx = parse_index(str(val))
                    if idx is not None:
                        rsrp = idx - 140.0
                if rsrq is None and "rsrqResult" in name and "0x" in str(val):
                    idx = parse_index(str(val))
                    if idx is not None:
                        rsrq = (idx / 2.0) - 19.5
                for child in node.get("children", []) or []:
                    if rsrp is not None and rsrq is not None:
                        return
                    visit(child)
                # outros valores compostos
                for k, v in node.items():
                    if rsrp is not None and rsrq is not None:
                        return
                    if k == "children":
                        continue
                    if isinstance(v, (dict, list)):
                        visit(v)
            elif isinstance(node, list):
                for item in node:
                    if rsrp is not None and rsrq is not None:
                        return
                    visit(item)

        visit(content)
        return rsrp, rsrq

    # ── Alarmes (iMaster FM website) ─────────────────────────────────
    def collect_alarms(self) -> List[dict]:
        """Coleta alarmes correntes filtrados pelos tipos do evento (oss.alarm_filter).

        Reusa a sessão 'monitoring' (bspsession+roarand do session.json); em sessão
        expirada, renova 1× via a máquina existente (_renew_session) e re-tenta. Retorna
        linhas achatadas prontas para db.insert_alarms_batch (dedup por csn já aplicado)."""
        names = (self.event.get("oss", {}) or {}).get("alarm_filter") or _DEFAULT_ALARM_NAMES
        try:
            catalog = _load_alarm_catalog()
        except Exception as e:
            logger.error(f"Falha ao carregar o catálogo de alarmes: {e}")
            return []
        pairs, missing = _resolve_alarm_pairs(names, catalog)
        if missing:
            logger.warning(f"Tipos de alarme ignorados (ausentes no catálogo): {missing}")
        if not pairs:
            logger.warning("Filtro de alarmes vazio/inválido — coleta de alarmes ignorada.")
            return []
        condition = _build_alarm_condition(pairs)

        retries = 2
        renewed_this_call = False
        for attempt in range(retries):
            try:
                model_id = self._create_alarm_model(condition)
                raw = self._collect_alarm_pages(model_id)
                return self._flatten_alarms(raw)
            except SessionExpiredError as e:
                logger.warning(f"Erro de sessão no collect_alarms: {e}")
                if renewed_this_call:
                    self._engage_backoff("monitoring")
                    logger.error("Sessão monitoring renovada mas ainda inválida — "
                                 "pausando coleta de alarmes (backoff).")
                    return []
                if attempt < retries - 1 and self._renew_session("monitoring"):
                    renewed_this_call = True
                    continue
                return []
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                logger.error(f"Erro de conexão ao coletar alarmes: {e}")
                return []
            except Exception as e:
                logger.error(f"Erro ao coletar alarmes: {e}")
                return []
        return []

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

    def collect_kpis(self) -> List[dict]:
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
        return rows

    def collect_vips(self, mode: str = "express") -> List[dict]:
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
        return rows

    def collect_alarms(self) -> List[dict]:
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
        return rows


# ── NullCollector (produção sem fonte configurada) ───────────────────

class NullCollector(BaseCollector):
    """Retorna listas vazias para evitar geração de dados mockados em produção."""
    def collect_kpis(self) -> List[dict]:
        return []

    def collect_vips(self, mode: str = "express") -> List[dict]:
        return []

    def collect_alarms(self) -> List[dict]:
        return []


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
