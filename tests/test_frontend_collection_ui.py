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
            assert page.locator("#sync-modal-body").get_by_text("Cobertura").count() == 1
            assert page.locator("#sync-modal-body").get_by_text("Regional ativa").count() == 1
            assert page.locator("#sync-modal-body").get_by_text("10.220.50.9").count() == 1
            assert page.locator("#sync-modal-body").get_by_text("Síncrono").count() == 1
            if scenario == "partial":
                assert page.locator("#sync-modal-body").get_by_text("18NLCTAL01GI").count() == 1
            if scenario == "error":
                assert page.locator("#sync-modal-body").get_by_text("Task 2072").count() == 1
                assert page.locator("#sync-modal-body").get_by_text("Task recusada pelo OSS").count() == 1
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_kpi_dropdown_expoe_somente_a_tecnologia_do_monitoring():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator("#metric-selector optgroup").first.wait_for(state="attached", timeout=5000)

            technologies = page.locator("#metric-selector option").evaluate_all(
                "options => options.map(option => option.textContent)"
            )
            groups = page.locator("#metric-selector optgroup").evaluate_all(
                "items => items.map(item => item.label)"
            )

            assert groups == ["KPIs 4G"]
            assert technologies
            assert all("· 4G ·" in label for label in technologies)
            assert all("5G" not in label for label in technologies)
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_kpi_dropdown_nrducell_expoe_somente_kpis_disponiveis_na_task_748():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                f"{url}/index.html?kpiTechnology=5G_NRDUCELL",
                wait_until="domcontentloaded",
            )
            page.locator("#metric-selector optgroup").first.wait_for(state="attached", timeout=5000)

            labels = page.locator("#metric-selector option").evaluate_all(
                "options => options.map(option => option.textContent)"
            )
            values = page.locator("#metric-selector option").evaluate_all(
                "options => options.map(option => option.value)"
            )
            groups = page.locator("#metric-selector optgroup").evaluate_all(
                "items => items.map(item => item.label)"
            )

            assert groups == ["KPIs 5G"]
            assert all("· 5G ·" in label for label in labels)
            assert set(values) == {
                "utilization_dl", "utilization_ul", "throughput_ul", "interference_ul",
            }
            assert "accessibility" not in values
            assert "availability" not in values
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def _close_chart_popup(page):
    modal = page.locator("#chart-popup-modal:not(.hidden)")
    if modal.count():
        page.locator("#popup-chart-close").click()
        page.locator("#chart-popup-modal.hidden").wait_for(state="attached", timeout=3000)


def test_lista_de_sites_nao_repete_nome_e_tech_selector_recorta_celulas():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator(".site-item").first.wait_for(state="visible", timeout=8000)

            names = page.locator(".site-item .site-name").evaluate_all(
                """els => els.map(el => {
                    const text = el.childNodes[0] ? el.childNodes[0].textContent : el.textContent;
                    return (text || "").trim();
                })"""
            )
            assert names
            assert len(names) == len(set(names))
            assert names.count("SPSMG7") == 1

            _close_chart_popup(page)
            page.locator('.site-item[data-id="SPSMG7"]').click()
            assert page.locator("#cell-selector").input_value() == "__media__"
            tech = page.locator("#tech-selector")
            tech.wait_for(state="visible", timeout=5000)

            tech.select_option("4G")
            page.wait_for_function(
                """() => [...document.querySelectorAll('#cell-selector option')]
                    .filter(o => o.value !== '__all__' && o.value !== '__media__').length === 12""",
                timeout=5000,
            )

            tech.select_option("5G")
            page.wait_for_function(
                """() => [...document.querySelectorAll('#cell-selector option')]
                    .filter(o => o.value !== '__all__' && o.value !== '__media__').length === 3""",
                timeout=5000,
            )
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


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


def test_media_e_site_completo_respeitam_familia_e_legendas():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{url}/index.html", wait_until="domcontentloaded")
            page.locator('.site-item[data-id="SPSMG7"]').wait_for(state="visible", timeout=8000)
            page.locator('.site-item[data-id="SPSMG7"]').click()
            _close_chart_popup(page)
            page.locator("#tech-selector:not(.hidden)").wait_for(state="visible", timeout=5000)
            page.locator("#tech-selector").select_option("all")

            page.locator("#cell-selector").select_option("__media__")
            page.wait_for_function(
                """() => {
                  const canvas = document.getElementById('kpi-chart');
                  const charts = Object.values(Chart.instances || {});
                  const chart = charts.find(c => c.canvas === canvas)
                    || (typeof Chart.getChart === 'function' ? Chart.getChart(canvas) : null);
                  return !!chart && chart.data.datasets.length === 2;
                }""",
                timeout=5000,
            )
            media_labels = _chart_dataset_labels(page, "kpi-chart")
            assert media_labels == ["Média 4G", "Média 5G"]

            page.locator("#cell-selector").select_option("__all__")
            page.locator("#chart-popup-modal:not(.hidden)").wait_for(state="visible", timeout=5000)
            page.wait_for_function(
                """() => {
                  const canvas = document.getElementById('popup-kpi-chart');
                  const charts = Object.values(Chart.instances || {});
                  const chart = charts.find(c => c.canvas === canvas)
                    || (typeof Chart.getChart === 'function' ? Chart.getChart(canvas) : null);
                  return !!chart && chart.data.datasets.length === 15
                    && chart.options.plugins.legend.display === true;
                }""",
                timeout=5000,
            )
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise
