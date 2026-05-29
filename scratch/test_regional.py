"""
Teste de viabilidade: regional alternativa 10.220.30.9

Valida se a infraestrutura HTTP do SmartEvents pode ser reutilizada
para regionais em IPs distintos sem modificar arquivos de produção.

Uso:
    python scratch/test_regional.py
    python scratch/test_regional.py --skip-login   # usa session_regional.json existente
"""

import argparse
import json
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
SESSION_FILE = DATA_DIR / "session_regional.json"

BASE_URL = "https://10.220.30.9:31943"
KPI_TASK_ID = 2001
VIP_TASK_ID = 14127

SECTION_SEP = "-" * 60


# ── Session helpers (espelha _build_session do collector.py) ──────────

def load_session(module: str) -> requests.Session:
    sess = requests.Session()
    sess.verify = False

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[ERRO] {SESSION_FILE} não encontrado. Execute sem --skip-login.")
        sys.exit(1)

    module_data = data.get(module, {})
    for cookie in module_data.get("cookies", []):
        sess.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))

    roarand = module_data.get("roarand")
    if roarand:
        sess.headers.update({"roarand": roarand})

    return sess


def check_session_valid(resp: requests.Response) -> bool:
    if resp.status_code in (401, 403):
        return False
    ct = resp.headers.get("Content-Type", "")
    if "text/html" in ct:
        body_lower = resp.text[:2000].lower()
        if any(k in body_lower for k in ["login", "sso", "authentication", "session"]):
            return False
    return True


# ── RSRP/RSRQ extraction (espelha _extract_rsrp_rsrq_from_json) ───────

def extract_rsrp_rsrq(content):
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


# ── Teste 1: Autenticação ──────────────────────────────────────────────

def test_auth(skip_login: bool) -> dict:
    result = {"status": "FALHOU", "elapsed_s": None, "modules": [], "error": None}

    if not skip_login:
        print(f"\n{SECTION_SEP}")
        print("AUTENTICAÇÃO — executando get_session_regional.py")
        print(SECTION_SEP)
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT_DIR / "scratch" / "get_session_regional.py"), "--headless"],
                capture_output=True, text=True, timeout=120
            )
            elapsed = round(time.time() - t0, 1)
            print(proc.stdout)
            if proc.returncode != 0:
                result["error"] = proc.stderr or proc.stdout
                print(f"[ERRO] get_session_regional.py saiu com código {proc.returncode}")
                return result
            result["elapsed_s"] = elapsed
        except subprocess.TimeoutExpired:
            result["error"] = "Timeout ao executar Playwright (>120s)"
            return result
        except Exception as e:
            result["error"] = str(e)
            return result
    else:
        print(f"\n[--skip-login] Usando {SESSION_FILE} existente.")

    if not SESSION_FILE.exists():
        result["error"] = f"{SESSION_FILE} não foi gerado."
        return result

    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    modules = []
    for mod in ("monitoring", "trace"):
        md = data.get(mod, {})
        if md.get("bspsession") and md.get("roarand"):
            modules.append(mod)

    result["modules"] = modules
    result["status"] = "OK" if modules else "FALHOU"
    if not result["elapsed_s"]:
        result["elapsed_s"] = "N/A (--skip-login)"
    return result


# ── Teste 2: KPI — task 2001 ───────────────────────────────────────────

def test_kpi() -> dict:
    result = {
        "http_status": None,
        "status": "FALHOU",
        "cells_found": 0,
        "sample_metrics": [],
        "last_exec_time": None,
        "raw_obj_count": 0,
        "error": None,
    }

    print(f"\n{SECTION_SEP}")
    print(f"KPI — POST monitor/task/result  (taskId={KPI_TASK_ID})")
    print(SECTION_SEP)

    sess = load_session("monitoring")
    now_ms = int(time.time() * 1000)
    url = f"{BASE_URL}/rest/oss/access/pm/v1/monitor/task/result?nocache={now_ms}"
    payload = [{
        "taskId": KPI_TASK_ID,
        "preExecTime": now_ms,
        "objNoExecTimes": []  # discovery mode — retorna todos os objetos da tarefa
    }]

    try:
        resp = sess.post(url, json=payload, timeout=30, headers={"x-non-renewal-session": "true"})
        result["http_status"] = resp.status_code
        print(f"  HTTP {resp.status_code}")

        if not check_session_valid(resp):
            result["error"] = "Sessão expirada ou redirecionamento SSO"
            return result

        resp.raise_for_status()
        data = resp.json()

        # Contar objetos retornados e coletar métricas de amostra
        metric_names = set()
        obj_count = 0
        last_exec = None

        for task_data in data.get("data", []):
            for res_item in task_data.get("results", []):
                exec_time = res_item.get("execTime") or task_data.get("execTime")
                if exec_time and not last_exec:
                    last_exec = datetime.utcfromtimestamp(exec_time / 1000.0).strftime('%Y-%m-%dT%H:%M:%SZ')

                items = res_item.get("objRes", []) if "objRes" in res_item else [res_item]
                for item in items:
                    obj_count += 1
                    for k in item.keys():
                        if k not in ("objNo", "objName", "obj", "execTime"):
                            metric_names.add(k)
                    # Buscar em counterRes se existir
                    for cr in item.get("counterRes", []):
                        if isinstance(cr, dict) and "name" in cr:
                            metric_names.add(cr["name"])

        result["raw_obj_count"] = obj_count
        result["cells_found"] = obj_count
        result["sample_metrics"] = sorted(list(metric_names))[:10]
        result["last_exec_time"] = last_exec
        result["status"] = "OK" if obj_count > 0 else "SEM_DADOS"

        print(f"  Objetos retornados: {obj_count}")
        print(f"  Métricas encontradas: {list(metric_names)[:5]}{'...' if len(metric_names) > 5 else ''}")
        print(f"  Última execTime: {last_exec}")

    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Sem conexão com {BASE_URL}: {e}"
        print(f"  [ERRO] {result['error']}")
    except requests.exceptions.Timeout:
        result["error"] = "Timeout (30s)"
        print(f"  [ERRO] {result['error']}")
    except Exception as e:
        result["error"] = str(e)
        print(f"  [ERRO] {result['error']}")

    return result


# ── Teste 3: Trace VIP — task 14127 (fluxo FARS 4 passos) ─────────────

def test_trace() -> dict:
    result = {
        "status": "FALHOU",
        "steps": {
            "pre_check": {"http_status": None, "check_state": None},
            "query_result": {"http_status": None, "msg_id": None},
            "filter": {"http_status": None, "meas_count": 0},
            "decode": {"http_status": None, "rsrp": None, "rsrq": None,
                       "site": None, "cell_id": None, "timestamp": None},
        },
        "error": None,
    }

    print(f"\n{SECTION_SEP}")
    print(f"TRACE VIP — fluxo FARS 4 passos  (taskId={VIP_TASK_ID})")
    print(SECTION_SEP)

    sess = load_session("trace")

    try:
        # Passo 1 — pre-check
        pre_url = f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/pre-check"
        pre_resp = sess.get(pre_url, params={
            "taskId": VIP_TASK_ID,
            "queryType": 0,
            "nocache": int(time.time() * 1000),
        }, timeout=30)
        result["steps"]["pre_check"]["http_status"] = pre_resp.status_code
        print(f"  Passo 1 pre-check: HTTP {pre_resp.status_code}")

        if not check_session_valid(pre_resp):
            result["error"] = "Sessão trace expirada no pre-check"
            return result
        if pre_resp.status_code >= 400:
            result["error"] = f"pre-check falhou: {pre_resp.text[:300]}"
            return result

        pre_data = pre_resp.json() if pre_resp.content else {}
        check_state = pre_data.get("checkState", False)
        result["steps"]["pre_check"]["check_state"] = check_state
        print(f"         checkState: {check_state}")
        if not check_state:
            result["error"] = f"checkState=False: {pre_data}"
            return result

        # Passo 2 — query/result para obter sess_msg_id
        result_url = f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/result"
        result_resp = sess.get(result_url, params={
            "nocache": int(time.time() * 1000),
            "startRow": 0,
            "pageSize": 10,
            "taskId": VIP_TASK_ID,
            "msgId": 1,
            "isSetBenchMarkTime": "false",
            "benchMarkTimeRowNo": -1,
        }, timeout=30)
        result["steps"]["query_result"]["http_status"] = result_resp.status_code
        print(f"  Passo 2 query/result: HTTP {result_resp.status_code}")

        if not check_session_valid(result_resp):
            result["error"] = "Sessão trace expirada no query/result"
            return result
        if result_resp.status_code >= 400:
            result["error"] = f"query/result falhou: {result_resp.text[:300]}"
            return result

        result_data = result_resp.json() if result_resp.content else {}
        data_block = result_data.get("data") or {}
        if isinstance(data_block, list):
            result["error"] = "query/result retornou lista — sem sess_msg_id"
            return result

        sess_msg_id = data_block.get("msgId")
        result["steps"]["query_result"]["msg_id"] = sess_msg_id
        print(f"         sess_msg_id: {sess_msg_id}")
        if not sess_msg_id:
            result["error"] = f"Sem msgId na resposta: {data_block}"
            return result

        # Passo 3 — filter-by-cols (RRC_MEAS_RPRT, mais recente primeiro)
        filter_url = f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols"
        now_ms = int(time.time() * 1000)
        _now_local = datetime.utcnow()
        filter_payload = {
            "colFilterDto": {
                "colFltExpSeq": [{
                    "fieldId": "Message Type",
                    "value": "RRC_MEAS_RPRT",
                    "operator": {"op": 0}
                }],
                "signalList": [],
                "hasStartTime": False,
                "startTime": "2000-01-01 00:00:00",
                "hasEndTime": False,
                "endTime": _now_local.strftime('%Y-%m-%d %H:%M:%S'),
                "isReverse": False
            },
            "pageDto": {
                "sqlColumnName": "Time",
                "isAscend": False,
                "taskId": VIP_TASK_ID,
                "msgId": sess_msg_id,
                "comparisonMsgId": -1,
                "startRow": 0,
                "pageSize": 1000,
                "templateName": [],
                "isSetBenchMarkTime": False,
                "benchMarkTimeRowNo": -1
            }
        }
        filter_resp = sess.post(
            filter_url + f"?nocache={now_ms}",
            json=filter_payload,
            timeout=60
        )
        result["steps"]["filter"]["http_status"] = filter_resp.status_code
        print(f"  Passo 3 filter-by-cols: HTTP {filter_resp.status_code}")

        if not check_session_valid(filter_resp):
            result["error"] = "Sessão trace expirada no filter-by-cols"
            return result
        if filter_resp.status_code >= 400:
            result["error"] = f"filter-by-cols falhou: {filter_resp.text[:300]}"
            return result

        filter_data = filter_resp.json() if filter_resp.content else {}
        data = filter_data.get("data") or {}
        table = data.get("tableData") or data.get("result") or (data if isinstance(data, list) else [])
        meas_count = len(table)
        result["steps"]["filter"]["meas_count"] = meas_count
        print(f"         RRC_MEAS_RPRT encontrados: {meas_count}")

        if meas_count == 0:
            result["status"] = "SEM_MEDIÇÕES"
            result["error"] = "Nenhuma mensagem RRC_MEAS_RPRT encontrada para este taskId"
            return result

        # Passo 4 — msg-explain-info para o primeiro registro (rowNo=1)
        explain_url = f"{BASE_URL}/rest/oss/access/fars/v1/traceresult/query/msg-explain-info"
        explain_resp = sess.get(explain_url, params={
            "nocache": int(time.time() * 1000),
            "taskId": VIP_TASK_ID,
            "msgId": sess_msg_id,
            "rowNo": 1,
            "tabularFlag": "y",
            "isSubscribe": "false",
            "isSecondDecode": "false",
        }, timeout=30)
        result["steps"]["decode"]["http_status"] = explain_resp.status_code
        print(f"  Passo 4 msg-explain-info: HTTP {explain_resp.status_code}")

        if explain_resp.status_code == 200:
            content = explain_resp.json()
            rsrp, rsrq = extract_rsrp_rsrq(content)
            result["steps"]["decode"]["rsrp"] = rsrp
            result["steps"]["decode"]["rsrq"] = rsrq
            print(f"         RSRP: {rsrp} dBm  |  RSRQ: {rsrq} dB")

        # Extrair site/cell do primeiro item da tabela filtrada
        first_item = table[0] if table else {}
        source = first_item.get("source", "")
        fields = {f.get("name", ""): f.get("value", "")
                  for f in (first_item.get("payload") or [])}
        cell_id = str(fields.get("GLCellId") or "")
        timestamp_raw = fields.get("Time")

        result["steps"]["decode"]["site"] = source
        result["steps"]["decode"]["cell_id"] = cell_id
        result["steps"]["decode"]["timestamp"] = timestamp_raw
        print(f"         Site receptor: {source}  |  CellId: {cell_id}")
        print(f"         Última medição: {timestamp_raw}")

        result["status"] = "OK"

    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Sem conexão com {BASE_URL}: {e}"
        print(f"  [ERRO] {result['error']}")
    except requests.exceptions.Timeout:
        result["error"] = "Timeout"
        print(f"  [ERRO] {result['error']}")
    except Exception as e:
        result["error"] = str(e)
        print(f"  [ERRO] {result['error']}")

    return result


# ── Relatório final Markdown ───────────────────────────────────────────

def build_report(auth: dict, kpi: dict, trace: dict) -> str:
    lines = []
    lines.append("# Relatório de Viabilidade — Regional Alternativa")
    lines.append(f"\n**IP testado:** `{BASE_URL}`  ")
    lines.append(f"**Data:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  ")
    lines.append(f"**KPI task_id:** `{KPI_TASK_ID}`  ")
    lines.append(f"**Trace task_id:** `{VIP_TASK_ID}`")

    # Autenticação
    lines.append("\n---\n")
    lines.append("## Autenticação")
    lines.append(f"- **Status:** {auth['status']}")
    lines.append(f"- **Tempo de login:** {auth['elapsed_s']}s")
    lines.append(f"- **Módulos capturados:** {auth['modules'] if auth['modules'] else 'nenhum'}")
    if auth["error"]:
        lines.append(f"- **Erro:** `{auth['error']}`")

    # KPI
    lines.append("\n---\n")
    lines.append(f"## KPI — Task {KPI_TASK_ID}")
    lines.append(f"- **HTTP Status:** {kpi['http_status']}")
    lines.append(f"- **Status:** {kpi['status']}")
    lines.append(f"- **Objetos retornados:** {kpi['cells_found']}")
    if kpi["sample_metrics"]:
        lines.append(f"- **Amostra de métricas:** `{', '.join(kpi['sample_metrics'])}`")
    if kpi["last_exec_time"]:
        lines.append(f"- **Última execTime:** `{kpi['last_exec_time']}`")
    if kpi["error"]:
        lines.append(f"- **Erro:** `{kpi['error']}`")

    # Trace
    lines.append("\n---\n")
    lines.append(f"## Trace VIP — Task {VIP_TASK_ID}")
    steps = trace["steps"]
    lines.append(f"- **Passo 1 (pre-check):** HTTP {steps['pre_check']['http_status']} — checkState={steps['pre_check']['check_state']}")
    lines.append(f"- **Passo 2 (query/result):** HTTP {steps['query_result']['http_status']} — msgId={steps['query_result']['msg_id']}")
    lines.append(f"- **Passo 3 (filter):** HTTP {steps['filter']['http_status']} — {steps['filter']['meas_count']} mensagens RRC_MEAS_RPRT")
    lines.append(f"- **Passo 4 (decode):** HTTP {steps['decode']['http_status']} — RSRP={steps['decode']['rsrp']} dBm / RSRQ={steps['decode']['rsrq']} dB")
    lines.append(f"- **Site receptor:** `{steps['decode']['site']}` | **Célula:** `{steps['decode']['cell_id']}`")
    lines.append(f"- **Última medição:** `{steps['decode']['timestamp']}`")
    if trace["error"]:
        lines.append(f"- **Erro:** `{trace['error']}`")

    # Conclusão
    lines.append("\n---\n")
    lines.append("## Conclusão")

    all_ok = auth["status"] == "OK" and kpi["status"] in ("OK", "SEM_DADOS") and trace["status"] in ("OK", "SEM_MEDIÇÕES")
    bloqueadores = []
    if auth["status"] != "OK":
        bloqueadores.append(f"Autenticação falhou: {auth.get('error', 'desconhecido')}")
    if kpi["status"] == "FALHOU":
        bloqueadores.append(f"KPI inacessível: {kpi.get('error', 'desconhecido')}")
    if trace["status"] == "FALHOU":
        bloqueadores.append(f"Trace FARS falhou: {trace.get('error', 'desconhecido')}")

    infra_ok = not bloqueadores
    lines.append(f"- **Infraestrutura reutilizável:** {'**SIM**' if infra_ok else '**NÃO**'}")
    if bloqueadores:
        lines.append("- **Bloqueadores encontrados:**")
        for b in bloqueadores:
            lines.append(f"  - {b}")
    else:
        lines.append("- **Bloqueadores encontrados:** nenhum")

    if infra_ok:
        lines.append("\n> A mesma infraestrutura de coleta (HttpCollector + fluxo FARS) pode ser reutilizada")
        lines.append("> para esta regional com ajuste apenas de `base_url`, `pm_task_id` e `task_id` dos VIPs no JSON do evento.")
    else:
        lines.append("\n> Verificar os bloqueadores acima antes de prosseguir.")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Teste de viabilidade regional alternativa")
    parser.add_argument("--skip-login", action="store_true",
                        help="Pula o login Playwright e usa session_regional.json existente")
    args = parser.parse_args()

    print("=" * 60)
    print(f" SmartEvents — Teste de Viabilidade Regional")
    print(f" Base URL: {BASE_URL}")
    print("=" * 60)

    auth = test_auth(skip_login=args.skip_login)
    if auth["status"] != "OK":
        print(f"\n[ABORTANDO] Login falhou — impossível testar endpoints.")
        report = build_report(auth, {
            "http_status": None, "status": "NÃO_TESTADO", "cells_found": 0,
            "sample_metrics": [], "last_exec_time": None, "raw_obj_count": 0, "error": "Login falhou"
        }, {
            "status": "NÃO_TESTADO", "steps": {
                "pre_check": {"http_status": None, "check_state": None},
                "query_result": {"http_status": None, "msg_id": None},
                "filter": {"http_status": None, "meas_count": 0},
                "decode": {"http_status": None, "rsrp": None, "rsrq": None,
                           "site": None, "cell_id": None, "timestamp": None},
            }, "error": "Login falhou"
        })
        print(f"\n{SECTION_SEP}\n")
        print(report)
        return

    kpi = test_kpi()
    trace = test_trace()

    report = build_report(auth, kpi, trace)

    print(f"\n{'=' * 60}")
    print(" RELATÓRIO FINAL")
    print(f"{'=' * 60}\n")
    print(report)

    # Salva o relatório em arquivo
    report_file = ROOT_DIR / "mae-api.md"
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[Relatório salvo em: {report_file}]")
    except Exception as e:
        print(f"\n[AVISO] Não foi possível salvar o relatório: {e}")


if __name__ == "__main__":
    main()
