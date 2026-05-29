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
SESSION_FILE = DATA_DIR / "session_regional.json"

# URLs da regional alternativa
BASE_URL = "https://10.220.30.9:31943"
LOGIN_URL = f"{BASE_URL}/unisso/login.action"
PM_URL = f"{BASE_URL}/ossfacewebsite/index.html#Access/Access_Performance_Monitor"
TRACE_URL = f"{BASE_URL}/ossfacewebsite/index.html#Access/Access_FARS_MENU_TASK_BROWSE"

# Credenciais da regional alternativa
USERNAME = "T3698285"
PASSWORD = "$162215Sr"

_existing_session: dict = {}
try:
    if SESSION_FILE.exists():
        with open(SESSION_FILE, "r", encoding="utf-8") as _f:
            _existing_session = json.load(_f)
except Exception:
    pass

session_data = {
    "trace": _existing_session.get("trace", {
        "bspsession": None,
        "roarand": None,
        "task_id": None,
        "cookies": []
    }),
    "monitoring": _existing_session.get("monitoring", {
        "bspsession": None,
        "roarand": None,
        "task_id": None,
        "obj_nos": [],
        "cookies": []
    }),
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
    global session_data
    url = request.url
    headers = request.headers

    if "/rest/oss/access/fars/" in url:
        roarand = headers.get("roarand") or headers.get("Roarand")
        if roarand:
            session_data["trace"]["roarand"] = roarand

        task_id = extract_task_id_from_url(url)
        if task_id:
            if session_data["trace"]["task_id"] != task_id:
                session_data["trace"]["task_id"] = task_id
                print(f"[PLAYWRIGHT] TRACE: taskId interceptado -> {task_id}")

        if request.method == "POST":
            try:
                payload = json.loads(request.post_data)
                if isinstance(payload, dict) and "taskId" in payload:
                    t_id = payload["taskId"]
                    if session_data["trace"]["task_id"] != t_id:
                        session_data["trace"]["task_id"] = t_id
                        print(f"[PLAYWRIGHT] TRACE: taskId (Body) -> {t_id}")
            except Exception:
                pass

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
                                print(f"[PLAYWRIGHT] PM: taskId interceptado -> {t_id}")
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
                                    print(f"[PLAYWRIGHT] PM: {new_added} novos objNos (Total: {len(session_data['monitoring']['obj_nos'])})")
            except Exception:
                pass

def main():
    parser = argparse.ArgumentParser(description="SmartEvents - Sessão Regional Alternativa")
    parser.add_argument("--headless", action="store_true", help="Modo headless")
    parser.add_argument("--module", choices=["trace", "monitoring", "both"], default="both")
    args = parser.parse_args()

    headless = args.headless
    target_module = args.module

    if not headless:
        print("=" * 60)
        print(f" SmartEvents - Sessão Regional: {BASE_URL}")
        print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if not headless:
            print("[PLAYWRIGHT] Inicializando Chromium...")
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
            print(f"[PLAYWRIGHT] Login: {LOGIN_URL}")
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
                print(f"[PLAYWRIGHT] Navegando para PM: {PM_URL}")
            try:
                page.goto(PM_URL, timeout=30000, wait_until="load")
                page.wait_for_timeout(10000)
            except Exception as e:
                if not headless:
                    print(f"[AVISO] Falha PM: {e}")

        # 3. Signaling Trace
        if target_module in ("trace", "both"):
            if not headless:
                print(f"[PLAYWRIGHT] Navegando para Trace: {TRACE_URL}")
            try:
                page.goto(TRACE_URL, timeout=30000, wait_until="load")
                page.wait_for_timeout(10000)
            except Exception as e:
                if not headless:
                    print(f"[AVISO] Falha Trace: {e}")

        # 4. Cookies e tokens globais
        cookies = context.cookies()
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
                print(f"[PLAYWRIGHT] bspsession capturado: {global_bsp[:15]}...")

        try:
            showedrand = page.evaluate("sessionStorage.getItem('u2020Showedrand')")
            if showedrand:
                session_data["trace"]["roarand"] = showedrand
                session_data["monitoring"]["roarand"] = showedrand
                if not headless:
                    print(f"[PLAYWRIGHT] roarand capturado: {showedrand[:15]}...")
        except Exception as e:
            if not headless:
                print(f"[AVISO] sessionStorage: {e}")

        # Gravação final
        trace_captured = session_data["trace"]["bspsession"] is not None and session_data["trace"]["roarand"] is not None
        monitoring_captured = session_data["monitoring"]["bspsession"] is not None and session_data["monitoring"]["roarand"] is not None

        if trace_captured or monitoring_captured:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=4)
            if not headless:
                print(f"\n[SUCESSO] Sessão salva em: {SESSION_FILE.absolute()}")
            else:
                print(f"[HEADLESS] Sessão renovada com sucesso.")
        else:
            print("\n[ERRO] Nenhuma sessão ou token pôde ser capturado.")
            sys.exit(1)

        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
