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
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Import requests safely without namespace package shadowing from the local workspace folder
import sys
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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


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
        # vips_by_task mapeia task_id -> nome do VIP, com o nome resolvido pela
        # tabela global `vips` (quando o JSON traz apenas {id, task_id}). Aceita
        # também o formato legado {name, task_id} pra retrocompat.
        self.vips_by_task = {}
        for v in event_config.get("vips", []):
            task_id = v.get("task_id")
            if task_id is None:
                continue
            name = v.get("name")
            if not name and v.get("id"):
                try:
                    g = db.get_vip(v["id"])
                    if g:
                        name = g["name"]
                except Exception:
                    pass
            if name:
                self.vips_by_task[task_id] = name
        self.thresholds = event_config.get("thresholds", {})
        self._running = False

    def _load_vips_by_task(self) -> dict:
        """Lê task_ids atuais do banco, capturando task_ids adicionados após o __init__."""
        result = {}
        try:
            for ev in db.get_event_vips(self.event_id):
                task_id = ev.get("task_id")
                name = ev.get("name")
                if task_id is not None and name:
                    result[task_id] = name
        except Exception:
            pass
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
    def collect_vips(self) -> List[dict]:
        """Retorna última leitura de RSRP/RSRQ de cada VIP."""
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

    def collect_vips(self) -> List[dict]:
        # CSV de trace de VIPs foi descontinuado: o esquema atual identifica
        # cada VIP pelo task_id da sua task dedicada no iManager (via HTTP).
        # Se você ainda precisa importar trace de CSV, ele teria que carregar
        # uma coluna explícita com o nome do VIP.
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
        "utilization_dl":    ["DL PRB USAGE", "DL PRB Usage"],
        "traffic_volume_dl": ["Traffic Volume DL", "{BRDC} Traffic Volume DL LTE", "Traffic Volume DL LTE", "{BRDC} NR DL Traffic Volume", "NR DL Traffic Volume"],
        "traffic_volume_ul": ["Traffic Volume UL", "{BRDC} Traffic Volume UL LTE", "Traffic Volume UL LTE", "{BRDC} NR UL Traffic Volume", "NR UL Traffic Volume"],
        "throughput_dl":     ["DL User Throughput", "{BRDC} DL User Throughput", "{BRDC} DL User Throughput LTE", "DL User Throughput LTE", "{BRDC} NR DL User Throughput", "NR DL User Throughput"],
        "throughput_ul":     ["UL User Throughput", "{BRDC} UL User Throughput", "{BRDC} UL User Throughput LTE", "UL User Throughput LTE", "{BRDC} NR UL User Throughput", "NR UL User Throughput"],
        "user_count":        ["{BRDC} Usuario", "Usuario", "Active Users", "{BRDC} User PCell"],
        "accessibility":     ["{BRDC} Acessibilidade", "Acessibilidade RRC", "ACC RRC", "Accessibility"],
    }

    # Backoff de renovação de sessão — persiste entre instâncias (collector recriado ao
    # trocar de evento).  Chave = nome do módulo ("trace" | "monitoring").
    _renew_failures: dict = {}        # módulo → nº de falhas consecutivas
    _renew_backoff_until: dict = {}   # módulo → datetime até quando não tentar

    def __init__(self, event_config: dict, base_url: str, session_cookie: str = ""):
        super().__init__(event_config)
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie
        self._session_monitoring = None
        self._session_trace = None
        self._renew_lock = threading.Lock()
        self._oss_tz_offset_min = event_config.get("oss", {}).get("timezone_offset_min", -180)

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

    def _load_session_data(self) -> dict:
        session_path = Path(__file__).parent.parent / "data" / "session.json"
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

        # Carrega cookies
        cookies = module_data.get("cookies", [])
        for cookie in cookies:
            sess.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))

        # Adiciona header CSRF (roarand)
        roarand = module_data.get("roarand")
        if roarand:
            sess.headers.update({"roarand": roarand})

        return sess

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
            return False
        ct = response.headers.get("Content-Type", "")
        if "text/html" in ct:
            body_lower = response.text[:2000].lower()
            if any(k in body_lower for k in ["login", "sso", "authentication", "session"]):
                return False
        return True

    def _renew_session(self, module: str):
        """
        Renova a sessão via Playwright em modo headless.

        Melhorias em relação à versão anterior:
        - Backoff exponencial: 60 s → 120 s → 180 s → … → 300 s entre tentativas
          após falhas consecutivas, para não travar a thread em loop infinito.
        - Detecção de renovação por outra thread: se o roarand mudou enquanto
          esperávamos o lock, apenas recarrega a sessão local sem rodar Playwright.
        - Log completo de stdout E stderr: o get_session.py escreve erros no stdout,
          então só logar stderr deixava a causa invisível.
        """
        from datetime import timedelta

        # ── Backoff: se falhou recentemente, pula esta tentativa ─────────
        now = datetime.utcnow()
        backoff_until = HttpCollector._renew_backoff_until.get(module)
        if backoff_until and now < backoff_until:
            remaining = int((backoff_until - now).total_seconds())
            logger.warning(
                f"[renew/{module}] Backoff ativo — pulando renovação por mais {remaining}s "
                f"({HttpCollector._renew_failures.get(module, 0)} falha(s) consecutiva(s))."
            )
            return

        # ── Captura roarand ANTES do lock para detectar renovação concorrente ──
        roarand_before = self._load_session_data().get(module, {}).get("roarand")

        with self._renew_lock:
            # Se outro thread renovou enquanto esperávamos, só recarrega sessão local
            roarand_now = self._load_session_data().get(module, {}).get("roarand")
            if roarand_now and roarand_now != roarand_before:
                logger.info(
                    f"[renew/{module}] Sessão já renovada por outra thread. "
                    "Recarregando sessão local sem rodar Playwright."
                )
                if module == "monitoring":
                    self._session_monitoring = None
                elif module == "trace":
                    self._session_trace = None
                return

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

            script_path = Path(__file__).parent.parent / "scratch" / "get_session.py"
            python_exe  = Path(__file__).parent.parent / ".venv" / "Scripts" / "python.exe"
            if not python_exe.exists():
                python_exe = Path("python")

            logger.info(
                f"[renew/{module}] Iniciando Playwright headless (timeout=120s) — "
                f"script: {script_path.name}"
            )
            try:
                result = subprocess.run(
                    [str(python_exe), str(script_path), "--headless", "--module", module],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(Path(__file__).parent.parent),
                )

                # get_session.py escreve erros no stdout (não stderr) — logamos os dois
                stdout_tail = result.stdout.strip()[-2000:] if result.stdout.strip() else ""
                stderr_tail = result.stderr.strip()[-500:]  if result.stderr.strip() else ""

                if result.returncode == 0:
                    logger.info(f"[renew/{module}] Playwright renovou a sessão com sucesso.")
                    if stdout_tail:
                        logger.debug(f"[renew/{module}] stdout:\n{stdout_tail}")
                    HttpCollector._renew_failures[module] = 0
                    HttpCollector._renew_backoff_until.pop(module, None)
                    if module == "monitoring":
                        self._session_monitoring = None
                    elif module == "trace":
                        self._session_trace = None
                else:
                    failures = HttpCollector._renew_failures.get(module, 0) + 1
                    HttpCollector._renew_failures[module] = failures
                    backoff_s = min(300, 60 * failures)   # 60 → 120 → 180 → 300 s
                    HttpCollector._renew_backoff_until[module] = (
                        datetime.utcnow() + timedelta(seconds=backoff_s)
                    )
                    logger.error(
                        f"[renew/{module}] Playwright falhou "
                        f"(returncode={result.returncode}, tentativa #{failures}). "
                        f"Próxima tentativa em {backoff_s}s.\n"
                        f"  STDOUT: {stdout_tail or '(vazio)'}\n"
                        f"  STDERR: {stderr_tail or '(vazio)'}"
                    )

            except subprocess.TimeoutExpired:
                failures = HttpCollector._renew_failures.get(module, 0) + 1
                HttpCollector._renew_failures[module] = failures
                backoff_s = min(300, 60 * failures)
                HttpCollector._renew_backoff_until[module] = (
                    datetime.utcnow() + timedelta(seconds=backoff_s)
                )
                logger.error(
                    f"[renew/{module}] Playwright TIMEOUT após 120s "
                    f"(tentativa #{failures}). Próxima tentativa em {backoff_s}s."
                )
            except Exception as e:
                failures = HttpCollector._renew_failures.get(module, 0) + 1
                HttpCollector._renew_failures[module] = failures
                backoff_s = min(300, 60 * failures)
                HttpCollector._renew_backoff_until[module] = (
                    datetime.utcnow() + timedelta(seconds=backoff_s)
                )
                logger.error(
                    f"[renew/{module}] Erro ao executar Playwright "
                    f"(tentativa #{failures}): {e}"
                )

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

        url = f"{self.base_url}/rest/oss/access/pm/v1/monitor/task/result?nocache={now_ms}"
        retries = 2
        for attempt in range(retries):
            s = self._get_session("monitoring")
            try:
                resp = s.post(url, json=payload, timeout=30, headers={"x-non-renewal-session": "true"})
                if not self._check_session_valid(resp, "monitoring"):
                    raise SessionExpiredError(f"Redirecionamento para SSO detectado no módulo monitoring")
                resp.raise_for_status()
                data = resp.json()
                return self._parse_kpi_response(data)
            except SessionExpiredError as e:
                logger.warning(f"Erro de sessão no collect_kpis: {e}")
                if attempt < retries - 1:
                    logger.warning(f"Sessão do módulo monitoring expirada. Iniciando renovação automática via Playwright...")
                    self._renew_session("monitoring")
                    continue
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
        data_list = response_json.get("data", [])
        if not data_list:
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
                                    logger.info(f"Mapeado dinamicamente objNo {obj_no_int} ({obj_name}) -> Celula {c_id}")
                                    break

                    if not cell_info:
                        continue

                    for metric, candidates in self.KPI_COLUMN_MAP.items():
                        val = self._find_metric_value(item, candidates)
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

        # Support iManager counterRes structures: {"name": "...", "value": "..."}
        if d.get("name") in candidates and "value" in d:
            return d.get("value")

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

    # Limite de mensagens RRC_MEAS_RPRT que tentamos decodificar por ciclo
    # (cada uma exige uma chamada extra ao msg-explain-info).
    _MAX_MEAS_DECODES_PER_CYCLE = 50
    _TRACE_PAGE_SIZE = 500

    def collect_vips(self) -> List[dict]:
        """
        Coleta resultados de trace via fluxo descoberto na UI do iManager:

          1) GET /traceresult/pre-check?taskId={id}&queryType=0
             → inicializa a sessão de query no backend FARS
          2) GET /traceresult/query/result?startRow=0&pageSize=N&taskId={id}&msgId=1&...
             → retorna {data.tableData[]} com payload em pares name/value
          3) Para cada RRC_MEAS_RPRT (limitado), GET /traceresult/query/msg-explain-info
             → traz o JSON decodificado contendo rsrpResult/rsrqResult

        Como cada VIP tem sua própria task de Signaling Trace no iManager,
        o nome do VIP é resolvido direto pelo task_id (1-para-1) — sem
        matching por IMSI.

        O endpoint /query/sort (que estava sendo usado antes) é apenas para
        reordenar resultados de uma sessão JÁ inicializada — chamá-lo sem
        pre-check retorna 500 "server communication linkage".
        """
        self.vips_by_task = self._load_vips_by_task()
        if not self.vips_by_task:
            logger.warning(
                "Nenhum VIP com task_id configurado — sem coleta de trace. "
                "Defina 'task_id' em cada VIP do evento."
            )
            return []

        measurements = []
        for task_id, vip_name in self.vips_by_task.items():
            retries = 2
            for attempt in range(retries):
                s = self._get_session("trace")
                try:
                    # 1) pre-check inicializa a sessão de query server-side
                    pre_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/pre-check"
                    pre_resp = s.get(pre_url, params={
                        "taskId": task_id,
                        "queryType": 0,
                        "nocache": int(time.time() * 1000),
                    }, timeout=30)
                    if not self._check_session_valid(pre_resp, "trace"):
                        raise SessionExpiredError("Sessão trace expirada (pre-check)")
                    if pre_resp.status_code >= 400:
                        self._handle_trace_error(task_id, "pre-check", pre_resp)
                        break
                    pre_data = pre_resp.json() if pre_resp.content else {}
                    if not pre_data.get("checkState", False):
                        logger.warning(
                            f"pre-check falhou para task {task_id}: {pre_data}"
                        )
                        break

                    # 2) query/result para obter sess_msg_id
                    result_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/result"
                    result_resp = s.get(result_url, params={
                        "nocache": int(time.time() * 1000),
                        "startRow": 0,
                        "pageSize": 10,   # mínimo para garantir sess_msg_id e sessão válida para filter-by-cols
                        "taskId": task_id,
                        "msgId": 1,
                        "isSetBenchMarkTime": "false",
                        "benchMarkTimeRowNo": -1,
                    }, timeout=30)
                    if not self._check_session_valid(result_resp, "trace"):
                        raise SessionExpiredError("Sessão trace expirada (query/result)")
                    if result_resp.status_code >= 400:
                        self._handle_trace_error(task_id, "query/result", result_resp)
                        break

                    result_data = result_resp.json() if result_resp.content else {}
                    data_block = result_data.get("data") or {}
                    if isinstance(data_block, list):
                        logger.warning(f"query/result retornou lista para task {task_id} — sem sess_msg_id")
                        break
                    sess_msg_id = data_block.get("msgId")
                    if not sess_msg_id:
                        logger.warning(f"Sem sess_msg_id para task {task_id}: {data_block}")
                        break

                    # 3) filter-by-cols — RRC_MEAS_RPRT, mais recente primeiro
                    filter_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols"
                    now_ms = int(time.time() * 1000)
                    # O iManager desserializa startTime/endTime no servidor antes de verificar
                    # hasStartTime/hasEndTime — enviar string vazia "" causa ROA_EXFRAME_EXCEPTION.
                    # Usar datas placeholder que abrangem todo o histórico possível.
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
                    filter_resp = s.post(
                        filter_url + f"?nocache={now_ms}",
                        json=filter_payload,
                        timeout=60
                    )
                    if not self._check_session_valid(filter_resp, "trace"):
                        raise SessionExpiredError("Sessão trace expirada (filter-by-cols)")
                    if filter_resp.status_code >= 400:
                        self._handle_trace_error(task_id, "filter-by-cols", filter_resp)
                        break

                    # 4) parsing + msg-explain-info para RSRP/RSRQ
                    measurements.extend(self._parse_filtered_trace_response(
                        filter_resp.json(), task_id, s, vip_name, sess_msg_id
                    ))
                    break
                except SessionExpiredError as e:
                    logger.warning(f"Erro de sessão no collect_vips: {e}")
                    if attempt < retries - 1:
                        logger.warning("Renovando sessão trace via Playwright...")
                        self._renew_session("trace")
                        continue
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
                    break
                except Exception as e:
                    logger.error(f"Erro ao consultar VIPs (taskId={task_id}): {e}")
                    break

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
                                       vip_name: str, sess_msg_id: int) -> List[dict]:
        """
        Parse do schema retornado por /traceresult/query/filter-by-cols.
        Como o filter-by-cols já filtrou por Message Type = RRC_MEAS_RPRT,
        não precisamos checar o tipo de mensagem no loop. A ordenação é decrescente
        por Time (mais recente primeiro), logo os primeiros _MAX_MEAS_DECODES_PER_CYCLE
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

        decodes_used = 0
        for idx, item in enumerate(table):
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

    def collect_vips(self) -> List[dict]:
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


# ── NullCollector (produção sem fonte configurada) ───────────────────

class NullCollector(BaseCollector):
    """Retorna listas vazias para evitar geração de dados mockados em produção."""
    def collect_kpis(self) -> List[dict]:
        return []

    def collect_vips(self) -> List[dict]:
        return []


# ── Factory ──────────────────────────────────────────────────────────

def build_collector(event_config: dict, mock: bool = False) -> BaseCollector:
    if mock:
        return MockCollector(event_config)

    oss = event_config.get("oss", {})
    import_folder = oss.get("import_folder", "")

    if import_folder and Path(import_folder).exists():
        return CsvCollector(event_config, import_folder)

    base_url = oss.get("base_url", "")
    if not base_url:
        base_url = "https://10.220.50.9:31943"

    return HttpCollector(event_config, base_url)
