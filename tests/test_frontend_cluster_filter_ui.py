"""Smoke tests do filtro de cluster no dashboard do app (Fase 4).

Mesmo padrão de tests/test_frontend_collection_ui.py: serve frontend/ estático e
dirige com Playwright contra os mocks reproduzíveis de bridge.js (MOCK_CLUSTERS).
"""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


FRONTEND_HTML = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
FRONTEND_APP = Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js"


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


def _chart_dataset_labels(page, canvas_id):
    return page.evaluate(
        """(id) => {
          const canvas = document.getElementById(id);
          if (!canvas) return [];
          const charts = Object.values(Chart.instances || {});
          const chart = charts.find(c => c.canvas === canvas)
            || (typeof Chart.getChart === 'function' ? Chart.getChart(canvas) : null);
          return chart ? chart.data.datasets.map(d => d.label) : [];
        }""",
        canvas_id,
    )


def test_dashboard_assets_tem_versao_para_evitar_cache_incompativel_do_webview():
    html = FRONTEND_HTML.read_text(encoding="utf-8")
    app = FRONTEND_APP.read_text(encoding="utf-8")

    assert 'href="css/main.css?v=' in html
    assert 'src="js/app.js?v=' in html
    assert "function _isVirtualKpiScope(siteId)" in app
    assert app.count("!_isVirtualKpiScope(State.selectedSite)") == 2


def test_cluster_selector_filtra_lista_e_mapa():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator(".site-item").first.wait_for(state="visible", timeout=8000)

            cluster_sel = page.locator("#cluster-selector")
            cluster_sel.wait_for(state="visible", timeout=5000)
            cluster_box = page.locator("#cluster-filter-row").bounding_box()
            controls_box = page.locator("#chart-controls").bounding_box()
            site_header_box = page.locator("#site-panel .panel-header").bounding_box()
            assert cluster_box and controls_box and site_header_box
            assert abs(cluster_box["y"] - controls_box["y"]) <= 1
            assert abs(cluster_box["height"] - controls_box["height"]) <= 1
            assert site_header_box["y"] >= cluster_box["y"] + cluster_box["height"] - 1
            assert page.locator("#site-panel > #cluster-filter-row").count() == 1
            assert page.locator("#chart-panel > #chart-controls").count() == 1
            options = cluster_sel.locator("option").all_inner_texts()
            assert any("Arquibancada Sul" in o for o in options)
            assert any("Campo (5G)" in o for o in options)

            cluster_sel.select_option("sul")
            page.wait_for_function(
                """() => document.querySelectorAll('#site-list .site-item').length === 3""",
                timeout=5000,
            )
            ids = page.locator("#site-list .site-item").evaluate_all(
                "els => els.map(el => el.dataset.id)"
            )
            assert set(ids) == {"cluster:sul", "ERB-07", "ERB-03"}

            cluster_sel.select_option("all")
            page.wait_for_function(
                """() => !document.querySelector('#site-list .site-item[data-id="cluster:sul"]')""",
                timeout=5000,
            )
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_selecionar_cluster_como_escopo_do_grafico_traz_serie_agregada():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator(".site-item").first.wait_for(state="visible", timeout=8000)

            page.locator("#cluster-selector").select_option("sul")
            page.locator('.site-item[data-id="cluster:sul"]').wait_for(
                state="visible", timeout=5000)
            page.locator('.site-item[data-id="cluster:sul"]').click()

            assert page.locator("#cell-selector").input_value() == "__media__"
            assert page.locator("#chart-site-label").inner_text() == "Cluster: Arquibancada Sul"

            page.wait_for_function(
                """() => {
                  const canvas = document.getElementById('kpi-chart');
                  const charts = Object.values(Chart.instances || {});
                  const chart = charts.find(c => c.canvas === canvas)
                    || (typeof Chart.getChart === 'function' ? Chart.getChart(canvas) : null);
                  return !!chart && chart.data.datasets.length > 0;
                }""",
                timeout=5000,
            )
            labels = _chart_dataset_labels(page, "kpi-chart")
            assert labels == ["Média 4G"]
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_layout_ampliado_e_comparacao_de_todos_os_clusters():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1912, "height": 1127})
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator(".site-item").first.wait_for(state="visible", timeout=8000)

            kpi_box = page.locator("#kpi-zone").bounding_box()
            site_box = page.locator("#site-panel").bounding_box()
            assert kpi_box and site_box
            assert abs(kpi_box["height"] - 220) <= 1
            assert abs(site_box["width"] - 240) <= 1

            selector = page.locator("#cluster-selector")
            assert "Comparar clusters" in selector.locator("option").all_inner_texts()
            selector.select_option("compare")
            page.wait_for_function(
                """() => document.querySelectorAll('#site-list .site-item').length === 2""",
                timeout=5000,
            )

            assert page.locator("#chart-site-label").inner_text() == "Comparativo de clusters"
            assert page.locator("#cell-selector").is_disabled()
            assert page.locator("#cell-selector").input_value() == "__media__"
            assert set(page.locator("#site-list .site-item").evaluate_all(
                "els => els.map(el => el.dataset.id)"
            )) == {"cluster:sul", "cluster:campo"}

            page.wait_for_function(
                """() => {
                  const canvas = document.getElementById('kpi-chart');
                  const chart = typeof Chart.getChart === 'function' ? Chart.getChart(canvas) : null;
                  return !!chart && chart.data.datasets.length === 3
                    && chart.data.datasets.every(dataset =>
                      dataset.data.some(value => Number.isFinite(value)));
                }""",
                timeout=5000,
            )
            assert _chart_dataset_labels(page, "kpi-chart") == [
                "Arquibancada Sul",
                "Campo (5G) · 4G",
                "Campo (5G) · 5G",
            ]

            page.locator("#metric-selector").select_option("throughput_dl")
            page.wait_for_function(
                """() => Chart.getChart(document.getElementById('kpi-chart'))
                  ?.data.datasets.length === 3""",
                timeout=5000,
            )
            page.locator("#tech-selector").select_option("5G")
            page.wait_for_function(
                """() => Chart.getChart(document.getElementById('kpi-chart'))
                  ?.data.datasets.length === 1""",
                timeout=5000,
            )
            assert _chart_dataset_labels(page, "kpi-chart") == ["Campo (5G)"]
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise
