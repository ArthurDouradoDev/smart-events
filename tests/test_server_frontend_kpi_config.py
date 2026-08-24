import json
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


HTML_PATH = Path(__file__).parents[1] / "server_frontend" / "index.html"
SERVER_FRONTEND_ROOT = HTML_PATH.parent


def test_event_form_configures_each_monitoring_object_type_explicitly():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="pm-task-list"' in html
    assert 'id="btn-add-pm-task"' in html
    assert "function addPmTaskRow" in html
    assert 'value="NRCELL"' in html
    assert 'value="NRDUCELL"' in html
    assert "integration: { pm_tasks: pmTasks }" in html
    assert "querySelectorAll('#pm-task-list .pm-task-row')" in html
    assert 'id="pm-task-4g"' not in html
    assert 'id="pm-task-nrcell"' not in html
    assert 'id="pm-task-nrducell"' not in html


def test_event_form_keeps_legacy_pm_task_as_4g_when_editing():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "if (!tasks.length && integration && Number(integration.pm_task_id) > 0)" in html
    assert "tasks.push({ tech: '4G', task_id: Number(integration.pm_task_id), period_seconds: DEFAULT_PM_PERIOD_SECONDS });" in html
    assert "renderPmTaskRows(configuredPmTasks(event.integration))" in html
    assert "'4G': 'pm-task-4g'" not in html
    assert "'5G_NRCELL': 'pm-task-nrcell'" not in html
    assert "'5G_NRDUCELL': 'pm-task-nrducell'" not in html


def test_event_list_counts_tasks_per_technology():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "${taskLabels[tech]} ×${pmCounts[tech]}${periodLabel(tech)}" in html


# ── Fase 2 — período da task (A1) ──────────────────────────────────────────

def test_pm_task_row_has_period_value_and_unit():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'class="pm-task-period-value"' in html
    assert 'class="pm-task-period-unit"' in html
    assert "function secondsToPeriodParts" in html
    assert "function periodPartsToSeconds" in html


def test_pm_task_period_is_persisted_in_seconds():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "period_seconds: periodPartsToSeconds(" in html
    assert "row.querySelector('.pm-task-period-value').value" in html
    assert "row.querySelector('.pm-task-period-unit').value" in html


def test_legacy_task_without_period_defaults_to_60s():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "const DEFAULT_PM_PERIOD_SECONDS = 60;" in html
    assert "Number(item && item.period_seconds) > 0" in html
    assert "period_seconds: DEFAULT_PM_PERIOD_SECONDS" in html


# ── Fase 2 — threshold com unidade (B2) ────────────────────────────────────

def test_threshold_payload_carries_value_and_unit():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "const th = (value, unit) => ({ value: parseFloat(value), unit });" in html
    assert "rsrp_warning:          th(document.getElementById('rsrp-warn').value, 'dBm')," in html
    assert "utilization_critical:  th(document.getElementById('util-crit').value, '%')," in html


def test_edit_event_reads_legacy_and_object_threshold_formats():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "const thVal = (raw, fallback) => (raw && typeof raw === 'object')" in html
    assert "thVal(event.thresholds.rsrp_warning, -100)" in html


# ── Fase 2 — verificação em navegador real (Playwright) ────────────────────

class _KpiConfigHandler(SimpleHTTPRequestHandler):
    captured_events_payload = None

    def log_message(self, *_args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/events") or self.path.startswith("/api/clientes"):
            self._json([])
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/parse-sites":
            self._json({
                "ok": True,
                "clusters": [],
                "sites": [{
                    "id": "SITE-A", "name": "SITE A", "lat": -23.7, "lng": -46.69,
                    "cells": [{"id": "SITE-A-1", "azimuth": 0, "tech": "4G", "frequency": "1800"}],
                }],
            })
            return
        if self.path == "/api/events":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            _KpiConfigHandler.captured_events_payload = json.loads(body)
            self._json({"ok": True})
            return
        self._json({"detail": "not found"}, 404)

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean == "/":
            relative = "index.html"
        elif clean.startswith("/static/"):
            relative = clean.removeprefix("/static/")
        else:
            relative = clean.lstrip("/")
        return str(SERVER_FRONTEND_ROOT / relative)


@contextmanager
def _kpi_config_server():
    _KpiConfigHandler.captured_events_payload = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KpiConfigHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_pm_task_period_helpers_round_trip_in_a_real_browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _kpi_config_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")

            assert page.evaluate("secondsToPeriodParts(900)") == {"value": 15, "unit": "min"}
            assert page.evaluate("secondsToPeriodParts(60)") == {"value": 1, "unit": "min"}
            assert page.evaluate("secondsToPeriodParts(30)") == {"value": 30, "unit": "s"}
            assert page.evaluate("periodPartsToSeconds(15, 'min')") == 900
            assert page.evaluate("periodPartsToSeconds(1, 'h')") == 3600

            # Task legada sem period_seconds assume o default de 60s (1 min).
            legacy = page.evaluate("configuredPmTasks({ pm_task_id: 747 })")
            assert legacy == [{"tech": "4G", "task_id": 747, "period_seconds": 60}]

            page.evaluate("renderPmTaskRows(configuredPmTasks({ pm_tasks: "
                           "[{ tech: 'NRCELL', task_id: 748, period_seconds: 900 }] }))")
            row = page.locator("#pm-task-list .pm-task-row").first
            assert row.locator(".pm-task-period-value").input_value() == "15"
            assert row.locator(".pm-task-period-unit").input_value() == "min"
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_submit_payload_carries_period_seconds_and_threshold_units():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _kpi_config_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")

            page.locator("#btn-novo-evento").click()
            page.locator("#event-name-input").fill("Evento Playwright")
            page.locator("#event-id-input").fill("evento-playwright")
            page.locator("#event-start-time").fill("2026-09-01T10:00")
            page.locator("#event-end-time").fill("2026-09-01T22:00")
            page.locator("#file-input").set_input_files({
                "name": "sites.csv", "mimeType": "text/csv",
                "buffer": b"site_id,site_name,lat,lng\nSITE-A,SITE A,-23.7,-46.69\n",
            })
            page.locator("#sites-info-msg").filter(has_text="Sucesso").wait_for(timeout=5000)
            # O desenho do poligono no mapa nao e o alvo deste teste; a area
            # minima valida e injetada diretamente na variavel global.
            page.evaluate("polygonCoordinates = [[-23.70,-46.70],[-23.69,-46.68],[-23.68,-46.70]]")

            page.locator("#btn-add-pm-task").click()
            row = page.locator("#pm-task-list .pm-task-row").last
            row.locator(".pm-task-tech").select_option("NRCELL")
            row.locator(".pm-task-id").fill("748")
            row.locator(".pm-task-period-value").fill("15")
            row.locator(".pm-task-period-unit").select_option("min")

            page.locator("#rsrp-warn").fill("-95")

            page.locator("#btn-submit").click()
            page.wait_for_timeout(300)

            payload = _KpiConfigHandler.captured_events_payload
            assert payload is not None, "POST /api/events não foi recebido"
            nrcell_task = next(t for t in payload["integration"]["pm_tasks"] if t["tech"] == "NRCELL")
            assert nrcell_task == {"tech": "NRCELL", "task_id": 748, "period_seconds": 900}
            assert payload["thresholds"]["rsrp_warning"] == {"value": -95, "unit": "dBm"}
            assert payload["thresholds"]["utilization_critical"] == {"value": 95, "unit": "%"}
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise
