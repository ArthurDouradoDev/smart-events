"""
Spy Playwright: abre o iManager visível e loga TODAS as requisições/respostas
do módulo FARS (/rest/oss/access/fars/...), comparando com o que o nosso
HttpCollector envia.

Uso:
  python scratch/spy_fars.py

Como usar:
  1) Quando o Chromium abrir, você verá o iManager logado automaticamente.
  2) Navegue até "Signaling Trace" -> abra a task 1925.
  3) Clique em "Query" / "Consultar" para puxar os resultados.
  4) Observe no terminal qual endpoint a UI chama e o que recebe de volta.
  5) Feche o navegador quando terminar; o log fica em scratch/fars_spy.log
"""

import json
import time
from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
LOG_PATH = ROOT / "scratch" / "fars_spy.log"

LOGIN_URL = "https://10.220.50.9:31943/unisso/login.action"
TRACE_URL = "https://10.220.50.9:31943/ossfacewebsite/index.html#Access/Access_FARS_MENU_TASK_BROWSE"
USERNAME = "T3524545"
PASSWORD = "41140362@del02"

log_file = open(LOG_PATH, "w", encoding="utf-8")


def log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    log_file.write(line + "\n")
    log_file.flush()


# guardamos url -> request info para casar com response
pending = {}


def on_request(request):
    url = request.url
    if "/rest/oss/access/fars/" not in url:
        return
    try:
        body = request.post_data
        body_short = body[:1500] if body else "(no body)"
    except Exception:
        body_short = "(unavailable)"

    headers = request.headers
    roarand = headers.get("roarand") or headers.get("Roarand") or "(none)"
    log("─" * 70)
    log(f"REQ  {request.method}  {url}")
    log(f"     roarand: {roarand[:30]}...")
    log(f"     content-type: {headers.get('content-type', '(none)')}")
    log(f"     body: {body_short}")
    pending[url] = time.time()


def on_response(response):
    url = response.url
    if "/rest/oss/access/fars/" not in url:
        return
    try:
        ct = response.headers.get("content-type", "")
        status = response.status
        # tentamos pegar o corpo
        try:
            text = response.text()
        except Exception:
            text = "(no body / binary)"
        log(f"RESP {status}  {url}")
        log(f"     content-type: {ct}")
        log(f"     body: {text[:2000]}")
    except Exception as e:
        log(f"RESP-ERR for {url}: {e}")


def main():
    log("Spy iniciado. Os logs vão para scratch/fars_spy.log")
    log(f"Login URL: {LOGIN_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--ignore-certificate-errors"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1366, "height": 800},
        )
        page = context.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        # Login
        try:
            page.goto(LOGIN_URL, timeout=30000, wait_until="load")
            page.wait_for_selector("#username", timeout=10000)
            page.fill("#username", USERNAME)
            page.wait_for_selector("#value", timeout=10000)
            page.fill("#value", PASSWORD)
            page.click("#submitDataverify")
            page.wait_for_timeout(7000)
            log("Login enviado.")
        except Exception as e:
            log(f"FALHA NO LOGIN: {e}")
            return

        # Vai para a página de Signaling Trace
        try:
            page.goto(TRACE_URL, timeout=30000, wait_until="load")
            log("Página de Signaling Trace aberta.")
        except Exception as e:
            log(f"Não foi possível abrir a página do Trace: {e}")

        log("")
        log("=" * 70)
        log("AÇÃO MANUAL: Na janela do navegador,")
        log("  1. Encontre a task 1925 na lista (use o filtro se precisar).")
        log("  2. Clique nela e em 'Query Result' / 'Consultar Resultados'.")
        log("  3. Aguarde a tabela carregar (ou o erro aparecer).")
        log("  4. Quando terminar, feche o navegador para encerrar o spy.")
        log("=" * 70)
        log("")

        # Mantem aberto até o user fechar
        try:
            while True:
                if not browser.is_connected():
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                browser.close()
            except Exception:
                pass
            log_file.close()


if __name__ == "__main__":
    main()
