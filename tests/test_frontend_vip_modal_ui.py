"""Smoke tests of the VIP analytics popup using the reproducible bridge mocks.

Segue o mesmo padrão de tests/test_frontend_collection_ui.py: servidor HTTP
temporário servindo `frontend/`, Playwright headless e skip automático se o
Chromium não estiver instalado. Evita comparação de pixels do canvas — o
gráfico é inspecionado via `Chart.instances` (posição dos pontos) e o
tooltip via DOM (é renderizado como HTML real por `vip.js`, não desenhado
no canvas), conforme a mitigação de risco descrita no plano desta feature.
"""

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
def _vip_modal_page(playwright, url, query="", viewport=None):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport=viewport)
    target = f"{url}/index.html" + (f"?{query}" if query else "")
    page.goto(target, wait_until="domcontentloaded")
    page.locator(".vip-card").first.wait_for(state="visible", timeout=10000)
    try:
        yield page
    finally:
        browser.close()


def _chart_point(page, index, dataset=0):
    """Coordenadas de viewport (x, y) do ponto `index` no gráfico do popup de VIP."""
    return page.evaluate(
        """([index, dataset]) => {
            const chart = Object.values(Chart.instances)
                .find(c => c.canvas.id === "vip-modal-chart");
            const meta = chart.getDatasetMeta(dataset);
            const el = meta.data[index];
            const rect = chart.canvas.getBoundingClientRect();
            return { x: rect.left + el.x, y: rect.top + el.y };
        }""",
        [index, dataset],
    )


def _run(playwright_factory, fn):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with playwright_factory() as url, sync_api.sync_playwright() as playwright:
            fn(url, playwright)
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_vip_modal_opens_as_wide_analytics_popup():
    def run(url, playwright):
        with _vip_modal_page(playwright, url, viewport={"width": 1366, "height": 768}) as page:
            page.locator(".vip-card").first.click()
            modal_box = page.locator("#vip-detail-modal .modal-box")
            modal_box.wait_for(state="visible", timeout=5000)

            box = modal_box.bounding_box()
            viewport = page.viewport_size
            assert box["width"] >= viewport["width"] * 0.75
            assert box["height"] >= viewport["height"] * 0.75

            # Covers AE1: resumo compacto e gráfico visíveis, sem botão de expansão.
            for field_id in ("vip-modal-site", "vip-modal-cell", "vip-modal-rsrp", "vip-modal-rsrq"):
                assert page.locator(f"#{field_id}").inner_text().strip() != ""
            assert page.locator("#vip-modal-chart-wrapper").is_visible()
            assert page.locator("#expand-vip-btn").count() == 0

    _run(lambda: _frontend_server(), run)


def test_vip_modal_tooltip_shows_contextual_metadata_per_index():
    def run(url, playwright):
        with _vip_modal_page(playwright, url) as page:
            page.locator(".vip-card").first.click()
            page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=5000)

            # Covers AE2: pontos em sites diferentes mostram cada um seu próprio site.
            pt0 = _chart_point(page, 0)
            page.mouse.move(pt0["x"], pt0["y"])
            tooltip = page.locator("#vip-modal-tooltip")
            tooltip.wait_for(state="visible", timeout=2000)
            assert "ERB-07 Interlagos" in tooltip.locator(".vip-tooltip-site").inner_text()
            assert "ERB-07-A1" in tooltip.locator(".vip-tooltip-cell").inner_text()

            pt4 = _chart_point(page, 4)
            page.mouse.move(pt4["x"], pt4["y"])
            assert "ERB-03 Av. Interlagos" in tooltip.locator(".vip-tooltip-site").inner_text()

            # Covers AE3: célula sem correspondência preserva a célula bruta e
            # marca o site como não identificado (nunca herda o site atual do VIP).
            pt3 = _chart_point(page, 3)
            page.mouse.move(pt3["x"], pt3["y"])
            assert "UNKNOWN-CELL-99" in tooltip.locator(".vip-tooltip-cell").inner_text()
            assert "não identificado" in tooltip.locator(".vip-tooltip-site").inner_text()

            # Covers AE4: métrica nula não vira "undefined" e o timestamp
            # duplicado (índices 5 e 6) não desalinha os metadados entre si.
            pt5 = _chart_point(page, 5)
            page.mouse.move(pt5["x"], pt5["y"])
            rsrp_text = tooltip.locator(".vip-tooltip-rsrp").inner_text()
            assert "undefined" not in rsrp_text
            assert rsrp_text.strip() == "—"
            assert "ERB-03-A1" in tooltip.locator(".vip-tooltip-cell").inner_text()

            pt6 = _chart_point(page, 6)
            page.mouse.move(pt6["x"], pt6["y"])
            assert "ERB-03-A2" in tooltip.locator(".vip-tooltip-cell").inner_text()
            assert "undefined" not in tooltip.locator(".vip-tooltip-rsrp").inner_text()

    _run(lambda: _frontend_server(), run)


def test_vip_modal_hover_line_tracks_active_point_and_clears_on_mouseout():
    def run(url, playwright):
        with _vip_modal_page(playwright, url) as page:
            page.locator(".vip-card").first.click()
            page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=5000)

            pt = _chart_point(page, 2)
            page.mouse.move(pt["x"], pt["y"])
            active = page.evaluate(
                """() => Object.values(Chart.instances)
                    .find(c => c.canvas.id === "vip-modal-chart")
                    .getActiveElements().length"""
            )
            assert active > 0
            assert page.locator("#vip-modal-tooltip").is_visible()

            # Sai da área do gráfico: a linha-guia (afterDraw, baseada nos
            # active elements) e o tooltip devem desaparecer.
            page.mouse.move(5, 5)
            active_after = page.evaluate(
                """() => Object.values(Chart.instances)
                    .find(c => c.canvas.id === "vip-modal-chart")
                    .getActiveElements().length"""
            )
            assert active_after == 0
            assert page.locator("#vip-modal-tooltip").is_hidden()

    _run(lambda: _frontend_server(), run)


def test_vip_modal_time_windows_switch_without_leaking_state():
    def run(url, playwright):
        with _vip_modal_page(playwright, url) as page:
            page.locator(".vip-card").first.click()
            page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=5000)

            for window in ("3d", "7d", "all", "today"):
                tab = page.locator(f'.time-tab[data-vip-window="{window}"]')
                tab.click()
                assert tab.get_attribute("aria-pressed") == "true"
                assert tab.evaluate("el => el.classList.contains('active')")
                # Sempre há dados nesta fixture (janela mínima "today" já cobre tudo).
                page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=2000)

            # Uma única instância de chart deve existir (sem vazamento ao trocar período).
            count = page.evaluate(
                """() => Object.values(Chart.instances)
                    .filter(c => c.canvas.id === "vip-modal-chart").length"""
            )
            assert count == 1

    _run(lambda: _frontend_server(), run)


def test_vip_modal_empty_window_shows_distinct_empty_state():
    def run(url, playwright):
        with _vip_modal_page(playwright, url, query="vipSeriesScenario=empty") as page:
            page.locator(".vip-card").first.click()
            page.locator("#vip-modal-no-data").wait_for(state="visible", timeout=5000)
            assert page.locator("#vip-modal-chart-wrapper").is_hidden()
            assert page.locator("#vip-modal-error").is_hidden()

    _run(lambda: _frontend_server(), run)


def test_vip_modal_error_state_is_distinct_and_retry_recovers():
    def run(url, playwright):
        with _vip_modal_page(playwright, url, query="vipSeriesScenario=error_once") as page:
            page.locator(".vip-card").first.click()
            page.locator("#vip-modal-error").wait_for(state="visible", timeout=5000)
            assert page.locator("#vip-modal-chart-wrapper").is_hidden()
            assert page.locator("#vip-modal-no-data").is_hidden()

            page.locator("#vip-modal-retry").click()
            page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=5000)
            assert page.locator("#vip-modal-error").is_hidden()

    _run(lambda: _frontend_server(), run)


def test_vip_modal_stale_response_never_overwrites_the_later_vip():
    def run(url, playwright):
        with _vip_modal_page(playwright, url, query="vipSeriesScenario=delay") as page:
            cards = page.locator(".vip-card")
            assert cards.count() >= 2
            # .vip-name pode incluir o emoji de coroa (VIP na célula atual); a
            # comparação usa o nome puro, igual ao exibido em #vip-modal-name.
            name_a = cards.nth(0).locator(".vip-name").inner_text().replace("👑", "").strip()
            name_b = cards.nth(1).locator(".vip-name").inner_text().replace("👑", "").strip()

            # Covers AE6: abre A (resposta lenta ainda em curso), fecha e abre B
            # antes da resposta de A voltar — ela não pode substituir o conteúdo de B.
            cards.nth(0).click()
            page.locator("#vip-detail-modal").wait_for(state="visible", timeout=5000)
            page.locator("#vip-modal-close").click()
            page.locator("#vip-detail-modal").wait_for(state="hidden", timeout=5000)
            cards.nth(1).click()

            page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=5000)
            shown_name = page.locator("#vip-modal-name").inner_text()
            assert shown_name == name_b

            # Espera além do atraso simulado: a resposta tardia de A não pode
            # reaparecer sobrescrevendo o conteúdo de B depois de renderizado.
            page.wait_for_timeout(1800)
            assert page.locator("#vip-modal-name").inner_text() == name_b
            assert page.locator("#vip-modal-chart-wrapper").is_visible()

    _run(lambda: _frontend_server(), run)


def test_vip_modal_closes_via_button_backdrop_and_escape_with_focus_restore():
    def run(url, playwright):
        with _vip_modal_page(playwright, url) as page:
            card = page.locator(".vip-card").first
            card.click()
            modal = page.locator("#vip-detail-modal")
            modal.wait_for(state="visible", timeout=5000)

            page.locator("#vip-modal-close").click()
            assert modal.is_hidden()

            card.click()
            modal.wait_for(state="visible", timeout=5000)
            # Clique interno não fecha.
            page.locator("#vip-modal-summary").click()
            assert modal.is_visible()
            # Clique no backdrop, longe do header (que fica acima do modal na pilha).
            page.locator("#vip-detail-modal").click(position={"x": 5, "y": 600})
            assert modal.is_hidden()

            card.click()
            modal.wait_for(state="visible", timeout=5000)
            page.keyboard.press("Escape")
            assert modal.is_hidden()
            assert page.evaluate("document.activeElement.classList.contains('vip-card')")

    _run(lambda: _frontend_server(), run)


@pytest.mark.parametrize("viewport", [
    {"width": 1366, "height": 768},
    {"width": 1024, "height": 768},
    {"width": 390, "height": 844},
])
def test_vip_modal_is_responsive_without_horizontal_overflow(viewport):
    def run(url, playwright):
        with _vip_modal_page(playwright, url, viewport=viewport) as page:
            page.locator(".vip-card").first.click()
            page.locator("#vip-modal-chart-wrapper").wait_for(state="visible", timeout=5000)

            # Covers AE7: fechar, filtros e gráfico continuam acessíveis; sem
            # overflow horizontal da página.
            overflow = page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
            )
            assert not overflow
            assert page.locator("#vip-modal-close").is_visible()
            assert page.locator('.time-tab[data-vip-window="7d"]').is_visible()

    _run(lambda: _frontend_server(), run)
