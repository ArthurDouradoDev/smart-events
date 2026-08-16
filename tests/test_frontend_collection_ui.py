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
