"""Smoke tests do filtro de alertas/alarmes pelo site selecionado.

Serve frontend/ estático e dirige com Playwright contra os mocks de bridge.js.
"""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_HTML = ROOT / "frontend" / "index.html"


@contextmanager
def _frontend_server():
    root = ROOT / "frontend"
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _skip_if_no_browser(exc):
    if "Executable doesn't exist" in str(exc):
        pytest.skip("Chromium do Playwright não está instalado neste ambiente")
    raise exc


def test_drawers_tem_checkbox_de_site_selecionado():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert 'id="alarms-event-only"' in html
    assert 'id="alarms-selected-only"' in html
    assert 'id="alerts-selected-only"' in html
    assert html.count("Somente site/cluster selecionado") == 2


def test_filtro_de_alarmes_e_alertas_respeita_o_site_selecionado():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            try:
                page.goto(f"{url}/index.html", wait_until="domcontentloaded")
                page.locator(".site-item").first.wait_for(state="visible", timeout=8000)

                page.locator("#alarms-btn").click()
                page.locator("#alarms-list .alarm-item").first.wait_for(
                    state="visible", timeout=5000)
                before = page.locator("#alarms-list .alarm-item").count()
                assert before >= 2

                page.locator("#alarms-selected-only").check()
                page.wait_for_function(
                    "() => document.querySelectorAll('#alarms-list .alarm-item').length === 1",
                    timeout=5000,
                )
                assert "ERB-07" in page.locator("#alarms-list .alarm-item").inner_text()

                page.locator("#alarms-drawer-close").click()
                page.locator(".site-item[data-id='ERB-03']").click()
                page.locator("#alarms-btn").click()
                page.wait_for_function(
                    "() => document.querySelectorAll('#alarms-list .alarm-item').length === 1",
                    timeout=5000,
                )
                assert "ERB-03" in page.locator("#alarms-list .alarm-item").inner_text()
                page.locator("#alarms-drawer-close").click()

                page.locator(".site-item[data-id='ERB-07']").click()
                page.locator("#alert-btn").click()
                page.locator("#alert-list .alert-item").first.wait_for(
                    state="visible", timeout=5000)
                assert page.locator("#alert-list .alert-item").count() == 3

                page.locator("#alerts-selected-only").check()
                page.wait_for_function(
                    "() => document.querySelectorAll('#alert-list .alert-item').length === 2",
                    timeout=5000,
                )
                titles = page.locator("#alert-list .alert-item-title").all_inner_texts()
                assert all("ERB-07" in t for t in titles)
                assert not any("ERB-03" in t for t in titles)
            finally:
                browser.close()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)
