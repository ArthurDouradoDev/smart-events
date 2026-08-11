"""Smoke tests of the collection modal using the reproducible bridge mocks."""

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


@contextmanager
def _frontend_server():
    root = Path(__file__).resolve().parents[1] / "frontend"
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize("scenario,label,css", [
    ("data", "Com dados", "st-data"),
    ("empty", "Sem novidade", "st-empty"),
    ("partial", "Parcial", "st-partial"),
    ("error", "Erro", "st-error"),
    ("auth_required", "Reautenticação necessária", "st-auth"),
    ("stale", "Desatualizado", "st-stale"),
])
def test_collection_modal_renders_all_operational_states(scenario, label, css):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{url}/index.html?collectionScenario={scenario}", wait_until="domcontentloaded")
            # O app entra em modo ativo depois de bridge.js habilitar o mock e
            # publica o status real pelo mesmo fluxo usado pelo operador.
            page.locator("#sync-indicator").wait_for(state="visible", timeout=5000)
            page.locator("#sync-indicator").click()
            assert page.locator("#sync-modal-body").get_by_text(label).count() >= 1
            assert page.locator(f".sync-dot.{css}").count() >= 1
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise
