import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from playwright.sync_api import sync_playwright, Request

# Diretores de caminhos do projeto
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
SESSION_FILE = DATA_DIR / "session.json"

# URLs
LOGIN_URL = "https://10.220.50.9:31943/unisso/login.action"
PM_URL = "https://10.220.50.9:31943/ossfacewebsite/index.html#Access/Access_Performance_Monitor"
TRACE_URL = "https://10.220.50.9:31943/ossfacewebsite/index.html#Access/Access_FARS_MENU_TASK_BROWSE"

# Credenciais
USERNAME = "T3524545"
PASSWORD = "41140362@del02"

session_data = {
    "trace": {
        "bspsession": None,
        "roarand": None,
        "task_id": None,
        "cookies": []
    },
    "monitoring": {
        "bspsession": None,
        "roarand": None,
        "task_id": None,
        "obj_nos": [],
        "cookies": []
    }
}

def extract_bspsession(cookie_header: str) -> str:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("bspsession="):
            return part.split("=", 1)[1]
    return None

def extract_task_id_from_url(url: str) -> int:
    try:
        parsed = urlparse(url)
        queries = parse_qs(parsed.query)
        if "taskId" in queries:
            return int(queries["taskId"][0])
    except Exception:
        pass
    return None

def monitor_requests(request: Request):
    """Monitora requisições de rede para capturar tokens e parâmetros dinâmicos."""
    global session_data
    url = request.url
    headers = request.headers

    # 1. Módulo de TRACE (FARS)
    if "/rest/oss/access/fars/" in url:
        roarand = headers.get("roarand") or headers.get("Roarand")
        if roarand:
            session_data["trace"]["roarand"] = roarand
            
        task_id = extract_task_id_from_url(url)
        if task_id:
            if session_data["trace"]["task_id"] != task_id:
                session_data["trace"]["task_id"] = task_id
                print(f"[PLAYWRIGHT] TRACE: ID da tarefa interceptado (Query) -> taskId: {task_id}")
            
        if request.method == "POST":
            try:
                payload = json.loads(request.post_data)
                if isinstance(payload, dict) and "taskId" in payload:
                    t_id = payload["taskId"]
                    if session_data["trace"]["task_id"] != t_id:
                        session_data["trace"]["task_id"] = t_id
                        print(f"[PLAYWRIGHT] TRACE: ID da tarefa interceptado (Body) -> taskId: {t_id}")
            except Exception:
                pass

    # 2. Módulo de MONITORING (KPIs / PM)
    elif "/rest/oss/access/pm/" in url:
        roarand = headers.get("roarand") or headers.get("Roarand")
        if roarand:
            session_data["monitoring"]["roarand"] = roarand
            
        if request.method == "POST":
            try:
                payload = json.loads(request.post_data)
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    if isinstance(item, dict):
                        if "taskId" in item:
                            t_id = item["taskId"]
                            if session_data["monitoring"]["task_id"] != t_id:
                                session_data["monitoring"]["task_id"] = t_id
                                print(f"[PLAYWRIGHT] PM MONITORING: ID da tarefa interceptado -> taskId: {t_id}")
                        if "objNoExecTimes" in item:
                            obj_nos = [x["objNo"] for x in item["objNoExecTimes"] if "objNo" in x]
                            if obj_nos:
                                current_nos = set(session_data["monitoring"]["obj_nos"])
                                new_added = 0
                                for obj in obj_nos:
                                    if obj not in current_nos:
                                        session_data["monitoring"]["obj_nos"].append(obj)
                                        new_added += 1
                                if new_added > 0:
                                    print(f"[PLAYWRIGHT] PM MONITORING: {new_added} novos objNos capturados (Total: {len(session_data['monitoring']['obj_nos'])})")
            except Exception:
                pass

def main():
    parser = argparse.ArgumentParser(description="SmartEvents - Automação de Sessão")
    parser.add_argument("--headless", action="store_true", help="Executa o navegador em modo headless (sem interface gráfica)")
    parser.add_argument("--module", choices=["trace", "monitoring", "both"], default="both", help="Módulo a renovar (trace, monitoring ou both)")
    args = parser.parse_args()

    headless = args.headless
    target_module = args.module

    if not headless:
        print("=" * 60)
        print(" SmartEvents - Automação de Sessão e Metadados")
        print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if not headless:
            print("[PLAYWRIGHT] Inicializando navegador Chromium...")
        browser = p.chromium.launch(
            headless=headless,
            args=["--ignore-certificate-errors"]
        )

        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1366, "height": 768}
        )

        page = context.new_page()
        page.on("request", monitor_requests)

        # 1. Login
        if not headless:
            print(f"[PLAYWRIGHT] Navegando para login: {LOGIN_URL}")
        try:
            page.goto(LOGIN_URL, timeout=30000, wait_until="load")
            page.wait_for_selector("#username", timeout=10000)
            page.fill("#username", USERNAME)
            page.wait_for_selector("#value", timeout=10000)
            page.fill("#value", PASSWORD)
            if not headless:
                print("[PLAYWRIGHT] Enviando formulário de login...")
            page.click("#submitDataverify")
        except Exception as e:
            print(f"[ERRO] Falha no login: {e}")
            browser.close()
            sys.exit(1)

        page.wait_for_timeout(7000)

        # 2. Performance Monitor
        if target_module in ("monitoring", "both"):
            if not headless:
                print(f"[PLAYWRIGHT] Navegando para Performance Monitoring: {PM_URL}")
            try:
                page.goto(PM_URL, timeout=30000, wait_until="load")
                page.wait_for_timeout(10000)
            except Exception as e:
                if not headless:
                    print(f"[AVISO] Falha ao ir para PM: {e}")

        # 3. Signaling Trace
        if target_module in ("trace", "both"):
            if not headless:
                print(f"[PLAYWRIGHT] Navegando para Signaling Trace: {TRACE_URL}")
            try:
                page.goto(TRACE_URL, timeout=30000, wait_until="load")
                page.wait_for_timeout(10000)
            except Exception as e:
                if not headless:
                    print(f"[AVISO] Falha ao ir para Trace: {e}")

        # 4. Fallbacks de Cookies e Tokens Globais
        cookies = context.cookies()
        # Salva todos os cookies para cada módulo (necessário para requests.Session)
        session_data["trace"]["cookies"] = [
            {"name": c["name"], "value": c["value"], "domain": c.get("domain", "")}
            for c in cookies
        ]
        session_data["monitoring"]["cookies"] = session_data["trace"]["cookies"]

        bspsessions = [c for c in cookies if c["name"] == "bspsession"]
        if bspsessions:
            global_bsp = bspsessions[0]["value"]
            session_data["trace"]["bspsession"] = global_bsp
            session_data["monitoring"]["bspsession"] = global_bsp
            if not headless:
                print(f"[PLAYWRIGHT] Cookie global 'bspsession' capturado: {global_bsp[:15]}...")


        try:
            showedrand = page.evaluate("sessionStorage.getItem('u2020Showedrand')")
            if showedrand:
                session_data["trace"]["roarand"] = showedrand
                session_data["monitoring"]["roarand"] = showedrand
                if not headless:
                    print(f"[PLAYWRIGHT] Token CSRF global capturado do sessionStorage: {showedrand[:15]}...")
        except Exception as e:
            if not headless:
                print(f"[AVISO] Não foi possível ler token do sessionStorage: {e}")

        # 5. Modo de Interação (apenas se não for headless)
        if not headless and (not session_data["trace"]["task_id"] or not session_data["monitoring"]["task_id"]):
            print("\n" + "*" * 60)
            print(" CAPTURA INTERATIVA DE METADADOS:")
            print(" O navegador continuará aberto por 60 segundos.")
            print(" Se desejar capturar os IDs de tarefa e a lista de células de forma automática:")
            print(" -> Na aba do Performance Monitor, clique em Consultar KPIs.")
            print(" -> Na aba de Signaling Trace, clique para ver os resultados da tarefa ativa.")
            print(" O script irá salvar os IDs assim que detectar os cliques.")
            print(" Se preferir, pode fechar a janela para encerrar e salvar apenas os tokens.")
            print("*" * 60 + "\n")

            start_time = time.time()
            interactive_timeout = 60

            try:
                while time.time() - start_time < interactive_timeout:
                    if not browser.is_connected():
                        print("[PLAYWRIGHT] Janela fechada pelo usuário. Encerrando captura.")
                        break

                    if session_data["trace"]["task_id"] and session_data["monitoring"]["task_id"] and len(session_data["monitoring"]["obj_nos"]) > 0:
                        print("[PLAYWRIGHT] Todos os metadados (taskIds e objNos) interceptados com sucesso!")
                        break

                    time.sleep(1)
            except Exception as e:
                print(f"[PLAYWRIGHT] Conexão interativa encerrada: {e}")

        # Gravação final
        trace_captured = session_data["trace"]["bspsession"] is not None and session_data["trace"]["roarand"] is not None
        monitoring_captured = session_data["monitoring"]["bspsession"] is not None and session_data["monitoring"]["roarand"] is not None

        if trace_captured or monitoring_captured:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=4)
            if not headless:
                print(f"\n[SUCESSO] Sessão salva em: {SESSION_FILE.absolute()}")
                print(json.dumps(session_data, indent=2))
            else:
                print(f"[HEADLESS] Sessão renovada com sucesso.")
        else:
            print("\n[ERRO] Nenhuma sessão ou token pôde ser capturado.")
            sys.exit(1)

        if not headless:
            print("\n[PLAYWRIGHT] Encerrando navegador.")
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

