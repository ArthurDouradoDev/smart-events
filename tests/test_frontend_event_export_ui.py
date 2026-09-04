from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _frontend_server():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_export_contract_is_wired_in_static_assets():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    bridge = (ROOT / "frontend" / "js" / "bridge.js").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "js" / "event_export.js").read_text(encoding="utf-8")

    assert 'id="event-data-modal"' in html
    assert 'value="consolidated" checked' in html
    assert 'value="combined" checked' in html
    assert 'id="export-include-vips" type="checkbox" checked' in html
    assert "initEventExport()" in app
    assert 'classList.add("rec-historical")' in app
    assert 'textContent = `DADOS ${storageStatus.db_size_mb} MB`' in app
    assert "previewEventExport" in bridge
    assert "startEventExport" in bridge
    assert "America/Sao_Paulo" in bridge
    assert "recIndicator.addEventListener" not in app
    assert "openClearFlow" in module


def test_mock_ui_exports_from_rec_and_preserves_clear_confirmation():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator("#rec-indicator").wait_for(state="visible", timeout=8000)
            page.locator("#rec-indicator").click()
            page.locator("#event-data-modal:not(.hidden)").wait_for(timeout=3000)

            page.locator("#event-data-export").click()
            assert page.locator('input[name="export-time"]:checked').get_attribute("value") == "consolidated"
            assert page.locator('input[name="export-tech"]:checked').get_attribute("value") == "combined"
            assert page.locator("#export-include-vips").is_checked()
            page.locator("#event-export-preview").get_by_text("KPIs").wait_for(timeout=3000)

            page.locator("#event-export-start").click()
            page.locator("#event-export-open-folder").wait_for(state="visible", timeout=5000)
            assert "Exportação concluída" in page.locator("#event-export-progress-label").inner_text()

            page.locator("#event-data-close").click()
            page.locator("#rec-indicator").click()
            page.locator("#event-data-clear").click()
            page.locator("#clear-history-modal:not(.hidden)").wait_for(timeout=2000)
            page.locator("#btn-confirm-clear-history-trigger").click()
            page.locator("#confirm-delete-modal:not(.hidden)").wait_for(timeout=2000)
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise
