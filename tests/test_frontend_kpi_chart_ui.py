"""Regressões visuais da Fase 4 do gráfico principal 4G/5G."""

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


def _chart_state(page):
    return page.evaluate(
        """() => {
          const chart = Chart.getChart(document.getElementById('kpi-chart'));
          return chart ? chart.data.datasets.map(dataset => ({
            label: dataset.label,
            color: dataset.borderColor,
            pointRadius: dataset.pointRadius,
          })) : [];
        }"""
    )


def _open_average_chart(page):
    page.locator('.site-item[data-id="SPSMG7"]').wait_for(
        state="visible", timeout=8000)
    page.locator('.site-item[data-id="SPSMG7"]').click()
    page.locator("#cell-selector").select_option("__media__")
    page.wait_for_function(
        """() => {
          const chart = Chart.getChart(document.getElementById('kpi-chart'));
          return !!chart && chart.data.datasets.length > 0;
        }""",
        timeout=5000,
    )


def _run_browser(scenario, assertion):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                f"{url}/index.html?kpiChart={scenario}",
                wait_until="domcontentloaded",
            )
            assertion(page)
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_ponto_isolado_recebe_marcador_visivel():
    def assertion(page):
        _open_average_chart(page)
        page.wait_for_function(
            """() => {
              const chart = Chart.getChart(document.getElementById('kpi-chart'));
              return chart?.data.datasets.some(
                dataset => dataset.label === 'Média 5G' && dataset.pointRadius > 0);
            }""",
            timeout=5000,
        )
        five_g = next(item for item in _chart_state(page) if item["label"] == "Média 5G")
        assert five_g["pointRadius"] > 0

    _run_browser("sparse", assertion)


def test_familia_sem_dado_mostra_motivo():
    def assertion(page):
        _open_average_chart(page)
        note = page.locator("#chart-series-note:not(.hidden)")
        note.wait_for(state="visible", timeout=5000)
        assert note.inner_text() == "5G sem dado nesta métrica"

    _run_browser("missing", assertion)


def test_filtro_oculto_nao_esvazia_o_grafico():
    def assertion(page):
        _open_average_chart(page)
        page.locator("#tech-selector").select_option("5G")
        # Restaura o layout completo como faria uma troca de escopo/evento e
        # seleciona um site só-4G mantendo o filtro anterior em State.
        page.evaluate(
            """async () => {
              const { default: State } = await import('/js/state.js');
              const { default: API } = await import('/js/bridge.js');
              State.set('sites', await API.getSiteLayout(State.eventId, null));
              State.set('selectedSite', 'ERB-07');
            }"""
        )
        page.locator("#tech-selector.hidden").wait_for(state="attached", timeout=5000)
        page.wait_for_function(
            """() => {
              const chart = Chart.getChart(document.getElementById('kpi-chart'));
              return chart?.data.datasets.some(dataset => dataset.label === 'Média 4G');
            }""",
            timeout=5000,
        )
        assert [item["label"] for item in _chart_state(page)] == ["Média 4G"]

    _run_browser("default", assertion)


def test_serie_sem_familia_nao_usa_a_cor_do_4g():
    def assertion(page):
        _open_average_chart(page)
        page.wait_for_function(
            """() => {
              const chart = Chart.getChart(document.getElementById('kpi-chart'));
              return chart?.data.datasets.some(
                dataset => dataset.label.includes('tecnologia não identificada'));
            }""",
            timeout=5000,
        )
        unknown = next(
            item for item in _chart_state(page)
            if "tecnologia não identificada" in item["label"])
        assert unknown["color"] == "#D29922"
        assert unknown["color"] != "#388BFD"

    _run_browser("unknown", assertion)
