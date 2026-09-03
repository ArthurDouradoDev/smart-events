"""Regressões do payload pequeno e do render incremental dos sites."""

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


@contextmanager
def _loaded_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator(".site-item").first.wait_for(state="visible", timeout=8000)
            page.locator(".leaflet-marker-icon").first.wait_for(state="attached", timeout=8000)
            try:
                yield page
            finally:
                browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_poll_de_status_nao_recria_marcadores_sem_mudanca():
    with _loaded_page() as page:
        calls = page.evaluate(
            """async () => {
              const State = (await import('/js/state.js')).default;
              const { renderSites } = await import('/js/map.js?v=20260903-site-poll-r1');
              let calls = 0;
              const original = L.Marker.prototype.setIcon;
              L.Marker.prototype.setIcon = function(icon) {
                if (this.options.pane !== 'badges') calls += 1;
                return original.call(this, icon);
              };
              renderSites(State.sites);
              L.Marker.prototype.setIcon = original;
              return calls;
            }"""
        )

        assert calls == 0


def test_mudanca_de_status_repinta_somente_o_site_afetado():
    with _loaded_page() as page:
        calls = page.evaluate(
            """async () => {
              const State = (await import('/js/state.js')).default;
              let calls = 0;
              const original = L.Marker.prototype.setIcon;
              L.Marker.prototype.setIcon = function(icon) {
                if (this.options.pane !== 'badges') calls += 1;
                return original.call(this, icon);
              };
              State.set('sites', State.sites.map(site => site.id === 'ERB-15'
                ? { ...site, status: 'critical', metric_value: 99 }
                : site));
              L.Marker.prototype.setIcon = original;
              return calls;
            }"""
        )

        assert calls == 1


def test_badges_de_vip_e_alarme_continuam_sincronizados():
    with _loaded_page() as page:
        page.evaluate(
            """async () => {
              const State = (await import('/js/state.js')).default;
              State.merge({
                vips: [...State.vips, {
                  id: 'vip-erb15', name: 'VIP ERB-15', in_event: true,
                  serving_site: 'ERB-15', status: 'ok',
                }],
                alarms: [...State.alarms, {
                  id: 'alarm-erb15', in_event: true, serving_site: 'ERB-15',
                  severity: 'Critical', name: 'Falha ERB-15',
                }],
              });
            }"""
        )

        assert page.locator(".site-badge svg[data-site='ERB-15'] .badge-vip").count() == 1
        assert page.locator(".site-badge svg[data-site='ERB-15'] .badge-alarm").count() == 1
        site_item = page.locator(".site-item[data-id='ERB-15']")
        assert site_item.locator(".vip-crown").count() == 1
        assert site_item.locator(".site-alarm").count() == 1

        page.evaluate(
            """async () => {
              const State = (await import('/js/state.js')).default;
              State.merge({
                vips: State.vips.filter(vip => vip.id !== 'vip-erb15'),
                alarms: State.alarms.filter(alarm => alarm.id !== 'alarm-erb15'),
              });
            }"""
        )

        assert page.locator(".site-badge svg[data-site='ERB-15']").count() == 0
        assert site_item.locator(".vip-crown").count() == 0
        assert site_item.locator(".site-alarm").count() == 0
