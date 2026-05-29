"""
core/session_renew.py — Renovação de sessão do iManager via Playwright.

Faz login no iManager (SPA), navega nos módulos de Performance Monitor e Signaling Trace e
captura os tokens de sessão (cookie `bspsession`, CSRF `roarand`) e metadados (task_id, obj_nos),
gravando tudo em `session.json`.

Usado em dois contextos:
  - Dev: `scratch/get_session.py` (CLI fino) chama `run(...)`.
  - .exe: `main.py --get-session` chama `run(...)`. Nesse caso o Chromium do Playwright é
    empacotado no bundle e localizado via PLAYWRIGHT_BROWSERS_PATH (ver _ensure_browsers_path).
"""

import json
import os
import sys
import time
import logging
from urllib.parse import urlparse, parse_qs
from pathlib import Path

logger = logging.getLogger(__name__)

# Credenciais do iManager (uso interno).
USERNAME = "T3524545"
PASSWORD = "41140362@del02"


def _ensure_browsers_path():
    """No .exe compilado, aponta o Playwright para os navegadores empacotados no bundle."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "ms-playwright"
        if bundled.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
            logger.info(f"PLAYWRIGHT_BROWSERS_PATH = {bundled}")
        else:
            logger.warning(f"Pasta de navegadores empacotada não encontrada: {bundled}")


def _extract_task_id_from_url(url: str):
    try:
        queries = parse_qs(urlparse(url).query)
        if "taskId" in queries:
            return int(queries["taskId"][0])
    except Exception:
        pass
    return None


def run(headless: bool = True, module: str = "both",
        base_url: str = "https://10.220.50.9:31943",
        session_file: str = None) -> int:
    """
    Renova a sessão do iManager. Retorna 0 em sucesso, 1 em falha.
    `module`: 'trace' | 'monitoring' | 'both'.
    """
    _ensure_browsers_path()

    # Import tardio: só carrega o Playwright quando a renovação é de fato acionada.
    from playwright.sync_api import sync_playwright, Request

    base_url = base_url.rstrip("/")
    login_url = f"{base_url}/unisso/login.action"
    pm_url = f"{base_url}/ossfacewebsite/index.html#Access/Access_Performance_Monitor"
    trace_url = f"{base_url}/ossfacewebsite/index.html#Access/Access_FARS_MENU_TASK_BROWSE"

    if session_file:
        session_path = Path(session_file)
    else:
        session_path = Path(__file__).parent.parent / "data" / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    # Preserva seções não renovadas por esta execução.
    existing = {}
    try:
        if session_path.exists():
            existing = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    session_data = {
        "trace": existing.get("trace", {
            "bspsession": None, "roarand": None, "task_id": None, "cookies": []
        }),
        "monitoring": existing.get("monitoring", {
            "bspsession": None, "roarand": None, "task_id": None, "obj_nos": [], "cookies": []
        }),
    }

    def monitor_requests(request: "Request"):
        url = request.url
        headers = request.headers
        if "/rest/oss/access/fars/" in url:
            roarand = headers.get("roarand") or headers.get("Roarand")
            if roarand:
                session_data["trace"]["roarand"] = roarand
            task_id = _extract_task_id_from_url(url)
            if task_id:
                session_data["trace"]["task_id"] = task_id
            if request.method == "POST":
                try:
                    payload = json.loads(request.post_data)
                    if isinstance(payload, dict) and "taskId" in payload:
                        session_data["trace"]["task_id"] = payload["taskId"]
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
                                session_data["monitoring"]["task_id"] = item["taskId"]
                            if "objNoExecTimes" in item:
                                obj_nos = [x["objNo"] for x in item["objNoExecTimes"] if "objNo" in x]
                                current = set(session_data["monitoring"]["obj_nos"])
                                for obj in obj_nos:
                                    if obj not in current:
                                        session_data["monitoring"]["obj_nos"].append(obj)
                except Exception:
                    pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--ignore-certificate-errors"])
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1366, "height": 768})
        page = context.new_page()
        page.on("request", monitor_requests)

        # 1. Login
        try:
            page.goto(login_url, timeout=30000, wait_until="load")
            page.wait_for_selector("#username", timeout=10000)
            page.fill("#username", USERNAME)
            page.wait_for_selector("#value", timeout=10000)
            page.fill("#value", PASSWORD)
            page.click("#submitDataverify")
        except Exception as e:
            logger.error(f"Falha no login: {e}")
            try:
                browser.close()
            except Exception:
                pass
            return 1

        page.wait_for_timeout(7000)

        # 2. Performance Monitor
        if module in ("monitoring", "both"):
            try:
                page.goto(pm_url, timeout=30000, wait_until="load")
                page.wait_for_timeout(10000)
            except Exception as e:
                logger.warning(f"Falha ao ir para PM: {e}")

        # 3. Signaling Trace
        if module in ("trace", "both"):
            try:
                page.goto(trace_url, timeout=30000, wait_until="load")
                page.wait_for_timeout(10000)
            except Exception as e:
                logger.warning(f"Falha ao ir para Trace: {e}")

        # 4. Cookies e tokens globais
        cookies = context.cookies()
        cookie_list = [
            {"name": c["name"], "value": c["value"], "domain": c.get("domain", "")}
            for c in cookies
        ]
        session_data["trace"]["cookies"] = cookie_list
        session_data["monitoring"]["cookies"] = cookie_list

        bspsessions = [c for c in cookies if c["name"] == "bspsession"]
        if bspsessions:
            global_bsp = bspsessions[0]["value"]
            session_data["trace"]["bspsession"] = global_bsp
            session_data["monitoring"]["bspsession"] = global_bsp

        try:
            showedrand = page.evaluate("sessionStorage.getItem('u2020Showedrand')")
            if showedrand:
                session_data["trace"]["roarand"] = showedrand
                session_data["monitoring"]["roarand"] = showedrand
        except Exception as e:
            logger.warning(f"Não foi possível ler token do sessionStorage: {e}")

        # 5. Modo interativo (apenas com janela visível, para captura de metadados em dev)
        if not headless and (not session_data["trace"]["task_id"] or not session_data["monitoring"]["task_id"]):
            print("\n" + "*" * 60)
            print(" CAPTURA INTERATIVA DE METADADOS (60s):")
            print(" -> No Performance Monitor, clique em Consultar KPIs.")
            print(" -> No Signaling Trace, abra os resultados da tarefa ativa.")
            print("*" * 60 + "\n")
            start = time.time()
            try:
                while time.time() - start < 60:
                    if not browser.is_connected():
                        break
                    if (session_data["trace"]["task_id"] and session_data["monitoring"]["task_id"]
                            and len(session_data["monitoring"]["obj_nos"]) > 0):
                        break
                    time.sleep(1)
            except Exception:
                pass

        # Gravação final
        trace_ok = session_data["trace"]["bspsession"] is not None and session_data["trace"]["roarand"] is not None
        mon_ok = session_data["monitoring"]["bspsession"] is not None and session_data["monitoring"]["roarand"] is not None

        if trace_ok or mon_ok:
            session_path.write_text(json.dumps(session_data, indent=4), encoding="utf-8")
            logger.info(f"Sessão renovada com sucesso em {session_path}")
            if not headless:
                print(f"[SUCESSO] Sessão salva em: {session_path.absolute()}")
            result = 0
        else:
            logger.error("Nenhuma sessão ou token pôde ser capturado.")
            result = 1

        try:
            browser.close()
        except Exception:
            pass
        return result
