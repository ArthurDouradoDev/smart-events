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


def _wait_map_settled(page, timeout=8000):
    """Espera o `fitToEvent` do arranque terminar de mexer na vista.

    Os marcadores nascem em `_poll(true)` com o zoom inicial (13) e só depois o
    `fitToEvent` muda o zoom; é o `zoomend` de map.js que reescreve as chaves de
    `_markerVisualKeys` com o zoom novo. Medir repintura entre essas duas etapas
    conta os cinco marcadores em vez de um — não porque o render incremental
    falhou, mas porque a chave de TODOS mudou junto.

    O sinal é a posição do marcador na tela — muda a cada quadro enquanto a
    vista se move. Como o `zoomend` dispara ao fim da animação, posição parada
    por vários quadros implica que ele já correu. Com a CPU estrangulada em 4x
    (CDP `Emulation.setCPUThrottlingRate`), medir sem esta espera pega as chaves
    defasadas em 10 de 12 execuções; com ela, 0 de 12.
    """
    page.wait_for_function(
        """() => {
          const marker = document.querySelector('.leaflet-marker-icon');
          if (!marker) return false;
          const box = marker.getBoundingClientRect();
          const key = `${Math.round(box.x)}:${Math.round(box.y)}`;
          const previous = window.__mapSettle;
          window.__mapSettle = previous && previous.key === key
            ? { key, hits: previous.hits + 1 }
            : { key, hits: 0 };
          return window.__mapSettle.hits >= 5;
        }""",
        timeout=timeout,
    )


@contextmanager
def _loaded_page(query=""):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.goto(f"{url}/index.html{query}", wait_until="domcontentloaded")
            page.locator(".site-item").first.wait_for(state="visible", timeout=8000)
            page.locator(".leaflet-marker-icon").first.wait_for(state="attached", timeout=8000)
            _wait_map_settled(page)
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


def test_tecnologia_sem_dado_fica_esmaecida_riscada_e_acessivel():
    with _loaded_page("?kpiChart=missing") as page:
        site = page.locator(".site-item[data-id='SPSMG7']")
        site.wait_for(state="visible", timeout=8000)
        four_g = site.locator(".site-tech-family", has_text="4G")
        five_g = site.locator(".site-tech-family", has_text="5G")

        assert four_g.count() == 1
        assert five_g.count() == 1
        assert "is-no-data" not in (four_g.get_attribute("class") or "")
        assert "is-no-data" in (five_g.get_attribute("class") or "")
        assert five_g.get_attribute("title") == "5G: sem dado nesta métrica"
        assert five_g.get_attribute("aria-label") == "5G: sem dado nesta métrica"

        style = five_g.evaluate(
            "element => ({ opacity: getComputedStyle(element).opacity, "
            "decoration: getComputedStyle(element).textDecorationLine })"
        )
        assert float(style["opacity"]) < 0.5
        assert "line-through" in style["decoration"]


def test_sem_trafego_esmaece_sem_riscar():
    with _loaded_page() as page:
        page.evaluate(
            """async () => {
              const State = (await import('/js/state.js')).default;
              State.set('sites', State.sites.map(site => site.id === 'SPSMG7'
                ? { ...site, technology_reasons: { '4G': 'ok', '5G': 'no_traffic' } }
                : site));
            }"""
        )
        five_g = page.locator(
            ".site-item[data-id='SPSMG7'] .site-tech-family.is-no-traffic",
            has_text="5G",
        )
        five_g.wait_for(state="visible", timeout=5000)

        assert five_g.get_attribute("title") == "5G: sem tráfego no período"
        style = five_g.evaluate(
            "element => ({ opacity: getComputedStyle(element).opacity, "
            "decoration: getComputedStyle(element).textDecorationLine })"
        )
        assert float(style["opacity"]) < 1
        assert "line-through" not in style["decoration"]
