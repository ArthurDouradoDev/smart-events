"""Fase 2 — seleção múltipla e fluxo de geração no Smart Events Central.

Os testes estruturais travam IDs, labels e funções do fluxo diretamente no HTML;
os testes de Playwright exercitam o fluxo real contra um servidor de mentira que
imita os endpoints de distribuição.
"""

import json
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


HTML_PATH = Path(__file__).parents[1] / "server_frontend" / "index.html"
SERVER_FRONTEND_ROOT = HTML_PATH.parent

PACKAGE_BYTES = b"PK\x03\x04-pacote-de-eventos-de-mentira"
FAILING_EVENT_ID = "evento-sem-cliente"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


# ── Testes estruturais ───────────────────────────────────────────────

def test_event_cards_expose_an_accessible_selection_checkbox():
    html = _html()

    assert 'class="event-select" id="event-select-${eventId}"' in html
    assert 'aria-label="Selecionar o evento ${escapeHTML(event.name)} para a distribuição"' in html
    assert 'onchange="toggleEventSelection(\'${eventId}\', this.checked)"' in html
    assert '<label class="event-select-label" for="event-select-${eventId}"' in html


def test_selection_checkbox_never_triggers_edit_or_delete():
    html = _html()

    # O checkbox para a propagação; Editar/Excluir continuam sendo os únicos
    # gatilhos de edição e exclusão.
    assert html.count('onclick="event.stopPropagation()"') >= 2
    assert 'onclick="editEvent(\'${escapeHTML(event.id)}\')"' in html
    assert 'onclick="deleteEvent(\'${escapeHTML(event.id)}\', event)"' in html
    assert "toggleEventSelection" in html
    assert "editEvent(" not in html.split("function toggleEventSelection")[1].split("}")[0]


def test_selection_bar_has_select_all_counter_and_generate_button():
    html = _html()

    assert 'id="select-all-visible-events"' in html
    assert "Selecionar todos visíveis" in html
    assert 'id="distribution-selection-count"' in html
    assert 'aria-live="polite"' in html
    assert 'id="btn-generate-distribution"' in html
    assert 'id="btn-clear-event-selection"' in html
    assert "function selectAllVisibleEvents" in html
    assert "function updateDistributionSelection" in html


def test_review_modal_lists_dependencies_reconciliation_and_exclusions():
    html = _html()

    assert '<dialog id="distribution-modal"' in html
    assert 'id="distribution-modal-title"' in html
    assert 'id="distribution-name-input"' in html
    assert "Arquivo sugerido" in html
    assert "Eventos incluídos" in html
    assert "Clientes e regionais" in html
    assert "sites · ${row.cells} células · ${row.clusters} clusters" in html
    assert "${row.pm_tasks} tasks PM" in html
    assert 'id="distribution-vip-fallback-note"' in html
    assert "fallback legado" in html
    assert 'id="distribution-reconciliation-note"' in html
    assert 'id="distribution-excluded"' in html
    assert "Não será incluído" in html


def test_blocking_error_disables_generate_and_warning_does_not():
    html = _html()

    # `preview.ok` é falso apenas quando há erro; aviso não entra nessa conta.
    assert "document.getElementById('btn-distribution-generate').disabled = !preview.ok;" in html
    assert 'distribution-issue error' in html
    assert 'distribution-issue warning' in html


def test_setup_format_is_disabled_without_capability():
    html = _html()

    assert "function renderDistributionFormats" in html
    assert "const usable = item.enabled && enabled.has(item.id);" in html
    assert "${usable ? '' : 'disabled'}" in html
    assert "(item.reason || 'indisponível')" in html
    # Sem resposta de capabilities, só o formato garantido desta fase aparece.
    assert "distributionCapabilities = { formats: ['event_package'], available_formats: [] };" in html


def test_progress_is_polled_with_backoff_and_stops_on_terminal_state():
    html = _html()

    assert "function pollDistributionJob" in html
    assert "distributionPollDelay = Math.min(Math.round(distributionPollDelay * 1.6), 4000);" in html
    assert "if (job.state === 'ready' || job.state === 'failed') return;" in html
    assert "function stopDistributionPolling" in html
    assert 'id="distribution-steps"' in html


def test_success_shows_size_hash_date_and_download_buttons():
    html = _html()

    assert 'id="btn-distribution-download"' in html
    assert 'id="btn-distribution-download-manifest"' in html
    assert "function formatDistributionBytes" in html
    assert "<span>SHA-256</span>" in html
    assert "<span>Tamanho</span>" in html
    assert "<span>Gerado em</span>" in html


def test_failure_shows_failed_step_copyable_diagnostic_and_retry():
    html = _html()

    assert 'id="distribution-failure"' in html
    assert 'id="distribution-diagnostic"' in html
    assert "A geração falhou na etapa" in html
    assert 'id="btn-distribution-retry"' in html
    assert "function retryDistributionJob" in html


def test_selection_survives_rerender_and_is_cleared_on_tab_switch_and_download():
    html = _html()

    assert "const selectedEventIds = new Set();" in html
    assert "${selectedEventIds.has(event.id) ? 'checked' : ''}" in html
    assert "if (tab !== 'eventos') { closeDistributionModal(); clearEventSelection(); }" in html
    assert "if (kind === 'download') {" in html
    # Evento excluído não pode continuar selecionado de forma invisível.
    assert "[...selectedEventIds].forEach(id => { if (!visible.has(id)) selectedEventIds.delete(id); });" in html


def test_frontend_never_sends_a_path_to_the_distribution_endpoints():
    html = _html()

    body = html.split("await fetch('/api/distributions', {")[1].split("});")[0]
    assert "event_ids" in body and "format" in body and "name" in body
    for forbidden in ("source", "output", "iss", "data_dir", "path"):
        assert f"{forbidden}:" not in body


# ── Servidor de mentira para os testes de Playwright ─────────────────

_EVENTS = [
    {
        "id": "evento-a", "name": "Barretos 2026", "status": "ACTIVE",
        "start_time": "2026-08-20T10:00:00Z", "end_time": "2026-08-25T22:00:00Z",
        "sites": [{"id": "S1", "cells": [{"id": "C1", "azimuth": 0}]}],
        "oss": {"cliente": "Vivo", "region": "SP"},
        "integration": {"pm_tasks": [{"tech": "4G", "task_id": 2279}]},
    },
    {
        "id": "evento-b", "name": "Rock 2026", "status": "SCHEDULED",
        "start_time": "2026-09-20T10:00:00Z", "end_time": "2026-09-25T22:00:00Z",
        "sites": [{"id": "S2", "cells": [{"id": "C2", "azimuth": 0}]}],
        "oss": {"cliente": "TIM", "region": "RJ"},
        "integration": {"pm_tasks": [{"tech": "4G", "task_id": 2280}]},
    },
    {
        "id": FAILING_EVENT_ID, "name": "Evento sem cliente", "status": "SCHEDULED",
        "start_time": "2026-10-01T10:00:00Z", "end_time": "2026-10-02T22:00:00Z",
        "sites": [], "oss": {"cliente": "Claro", "region": "MG"}, "integration": {},
    },
]

_CAPABILITIES = {
    "schema_version": 1,
    "formats": ["event_package"],
    "available_formats": [
        {"id": "event_package", "label": "Pacote de eventos (.sepack)",
         "description": "Para quem ja tem o SmartEvents instalado.", "enabled": True},
        {"id": "full_setup", "label": "Instalador completo (.exe)",
         "description": "Primeira instalacao ou atualizacao completa.",
         "enabled": False, "reason": "disponivel apos configurar um build-base"},
    ],
    "max_events": 50,
    "vip_policies": ["auto", "explicit", "none"],
    "app_version": "1.0.0",
}


def _preview_for(event_ids):
    rows = [event for event in _EVENTS if event["id"] in event_ids]
    if FAILING_EVENT_ID in event_ids:
        return {
            "ok": False, "name": "Evento sem cliente", "event_ids": list(event_ids),
            "clients": [], "events": [], "clientes": [], "vips": [],
            "explicit_vip_ids": [], "fallback_vip_ids": [], "logos": [],
            "counts": {"events": 1, "clientes": 0, "vips": 0, "logos": 0, "regions": 0,
                       "sites": 0, "cells": 0, "clusters": 0, "pm_tasks": 0},
            "excluded": ["credentials.json"], "warnings": [],
            "errors": [{"code": "reference.client_missing", "path": "events/x.json",
                        "message": "Cliente nao cadastrado para o evento."}],
            "suggested_filename": "SmartEvents_Eventos_x_20260831.sepack",
            "reconciliation_note": "Cliente, regional, VIP e logo sao conciliados pelo id.",
            "format": "event_package",
        }
    return {
        "ok": True, "name": "Dois clientes", "event_ids": list(event_ids),
        "clients": ["TIM", "Vivo"],
        "events": [{
            "id": event["id"], "name": event["name"], "status": event["status"],
            "client": event["oss"]["cliente"], "region": event["oss"]["region"],
            "sites": len(event["sites"]), "cells": 1, "clusters": 0, "pm_tasks": 1,
        } for event in rows],
        "clientes": [
            {"id": "vivo", "name": "Vivo", "regions": ["SP"], "logo": "vivo.png"},
            {"id": "tim", "name": "TIM", "regions": ["RJ"], "logo": ""},
        ],
        "vips": [
            {"id": "vip-vivo", "name": "Diretor Vivo", "client": "Vivo",
             "region": "SP", "source": "fallback"},
            {"id": "vip-tim", "name": "Diretor TIM", "client": "TIM",
             "region": "RJ", "source": "fallback"},
        ],
        "explicit_vip_ids": [], "fallback_vip_ids": ["vip-tim", "vip-vivo"],
        "logos": ["vivo.png"],
        "counts": {"events": len(rows), "clientes": 2, "vips": 2, "logos": 1, "regions": 2,
                   "sites": len(rows), "cells": len(rows), "clusters": 0, "pm_tasks": len(rows)},
        "excluded": ["credentials.json", "session.json e cookies", "bancos smart_events*.db"],
        "warnings": ["Fallback de compatibilidade: 2 VIPs foram incluidos por cliente + regional."],
        "errors": [],
        "suggested_filename": "SmartEvents_Eventos_Dois-clientes_20260831.sepack",
        "reconciliation_note": "Cliente, regional, VIP e logo sao conciliados pelo id.",
        "format": "event_package",
    }


class _DistributionHandler(SimpleHTTPRequestHandler):
    """Imita os endpoints da Fase 2. O job vira `ready` (ou `failed`) na 2ª consulta."""

    polls: dict = {}

    def log_message(self, *_args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _job(self, job_id, state, **fields):
        steps = ["queued", "validating", "packaging", "ready"]
        reached = steps[: steps.index(state) + 1] if state in steps else steps[:3]
        return {
            "job_id": job_id, "format": "event_package", "state": state,
            "name": "Dois clientes", "event_ids": ["evento-a", "evento-b"],
            "steps": [{"state": item, "at": "2026-08-31T12:00:00Z"} for item in reached],
            "progress": {"step": state, "steps": steps,
                         "index": steps.index(state) if state in steps else -1, "total": 3},
            "counts": {"events": 2, "clientes": 2, "vips": 2},
            "warnings": [], "errors": [], "failed_step": "", "diagnostic": "",
            "artifacts": {}, "manifest": None, "finished_at_utc": "",
            **fields,
        }

    def do_GET(self):
        clean = self.path.split("?", 1)[0]
        if clean == "/api/events":
            self._json(_EVENTS)
            return
        if clean in ("/api/clientes", "/api/vips"):
            self._json([])
            return
        if clean == "/api/distributions/capabilities":
            self._json(_CAPABILITIES)
            return
        if clean.startswith("/api/distributions/") and clean.endswith("/download"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="pacote.sepack"')
            self.send_header("Content-Length", str(len(PACKAGE_BYTES)))
            self.end_headers()
            self.wfile.write(PACKAGE_BYTES)
            return
        if clean.startswith("/api/distributions/"):
            job_id = clean.rsplit("/", 1)[-1]
            seen = _DistributionHandler.polls.get(job_id, 0) + 1
            _DistributionHandler.polls[job_id] = seen
            if seen < 2:
                self._json(self._job(job_id, "packaging"))
            elif job_id.startswith("fail"):
                self._json(self._job(
                    job_id, "failed", failed_step="packaging",
                    diagnostic="disco cheio ao gravar <caminho>/payload.zip",
                    errors=[{"code": "job.failed", "path": "",
                             "message": "Falha ao gerar a distribuicao."}],
                    finished_at_utc="2026-08-31T12:00:05Z",
                ))
            else:
                self._json(self._job(
                    job_id, "ready",
                    artifacts={"package": {
                        "name": "pacote.sepack", "bytes": len(PACKAGE_BYTES),
                        "sha256": "a" * 64, "media_type": "application/octet-stream",
                    }},
                    finished_at_utc="2026-08-31T12:00:05Z",
                ))
            return
        super().do_GET()

    def do_POST(self):
        clean = self.path.split("?", 1)[0]
        if clean == "/api/distributions/preview":
            self._json(_preview_for(self._read_json().get("event_ids") or []))
            return
        if clean == "/api/distributions":
            payload = self._read_json()
            prefix = "fail" if FAILING_EVENT_ID in (payload.get("event_ids") or []) else "ok"
            self._json(self._job(f"{prefix}{'0' * 28}", "queued"))
            return
        if clean == "/api/parse-sites":
            self._json({"ok": True, "sites": [], "clusters": []})
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
def _distribution_server():
    _DistributionHandler.polls = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DistributionHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@contextmanager
def _central_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _distribution_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#event-select-evento-a").wait_for(timeout=5000)
            try:
                yield page
            finally:
                browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def _select(page, *event_ids):
    for event_id in event_ids:
        page.locator(f"#event-select-{event_id}").check()


# ── Testes de Playwright ─────────────────────────────────────────────

def test_selecting_events_never_opens_the_edit_form():
    with _central_page() as page:
        _select(page, "evento-a")

        assert page.locator("#eventos-form-view").is_visible() is False
        assert page.locator("#eventos-list-view").is_visible() is True
        assert page.locator("#distribution-selection-count").inner_text() == "1 evento selecionado"
        assert page.locator("#btn-generate-distribution").is_disabled() is False


def test_select_all_visible_marks_every_card_and_clearing_resets_it():
    with _central_page() as page:
        page.locator("#select-all-visible-events").check()

        assert page.locator("#events-list .event-select:checked").count() == len(_EVENTS)
        assert "3 eventos selecionados" in page.locator("#distribution-selection-count").inner_text()

        page.locator("#btn-clear-event-selection").click()
        assert page.locator("#events-list .event-select:checked").count() == 0
        assert page.locator("#btn-generate-distribution").is_disabled() is True


def test_review_modal_shows_names_counts_and_the_legacy_vip_warning():
    with _central_page() as page:
        _select(page, "evento-a", "evento-b")
        page.locator("#btn-generate-distribution").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)

        review = page.locator("#distribution-preview-content").inner_text()
        assert "Barretos 2026" in review and "Rock 2026" in review
        assert "2 eventos · 2 clientes · 2 regionais · 2 VIPs" in review
        assert "Vivo" in review and "TIM" in review
        assert "credentials.json" in review

        fallback = page.locator("#distribution-vip-fallback-note")
        assert fallback.is_visible() is True
        assert "fallback legado" in fallback.inner_text()
        # Aviso não bloqueia a geração.
        assert page.locator("#btn-distribution-generate").is_disabled() is False

        # O Setup completo aparece, desabilitado e com o motivo.
        setup = page.locator("#distribution-format-full_setup")
        assert setup.is_disabled() is True
        assert "build-base" in page.locator("#distribution-format-list").inner_text()


def test_blocking_error_keeps_the_generate_button_disabled():
    with _central_page() as page:
        _select(page, FAILING_EVENT_ID)
        page.locator("#btn-generate-distribution").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)

        assert "reference.client_missing" in page.locator("#distribution-preview-content").inner_text()
        assert page.locator("#btn-distribution-generate").is_disabled() is True


def test_job_progress_reaches_ready_and_downloads_the_package():
    with _central_page() as page:
        _select(page, "evento-a", "evento-b")
        page.locator("#btn-generate-distribution").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)
        page.locator("#btn-distribution-generate").click()

        page.locator("#distribution-progress").wait_for(state="visible", timeout=5000)
        page.locator("#distribution-success").wait_for(state="visible", timeout=15000)

        success = page.locator("#distribution-success").inner_text()
        assert "pacote.sepack" in success
        assert "a" * 64 in success
        assert "2026-08-31T12:00:05Z" in success

        with page.expect_download(timeout=10000) as download:
            page.locator("#btn-distribution-download").click()
        assert download.value.suggested_filename.endswith(".sepack")

        # Concluído o download, a seleção é limpa e o modal fecha.
        page.locator("#distribution-modal").wait_for(state="hidden", timeout=5000)
        assert page.locator("#events-list .event-select:checked").count() == 0


def test_failed_job_shows_the_step_diagnostic_and_retry_button():
    with _central_page() as page:
        _select(page, FAILING_EVENT_ID, "evento-a")
        page.locator("#btn-generate-distribution").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)
        # A revisão bloqueia; o job de falha é exercitado forçando o envio.
        page.evaluate("startDistributionJob()")
        page.locator("#distribution-failure").wait_for(state="visible", timeout=15000)

        failure = page.locator("#distribution-failure").inner_text()
        assert "packaging" in failure
        assert "job.failed" in failure
        assert "payload.zip" in page.locator("#distribution-diagnostic").inner_text()
        assert page.locator("#btn-distribution-retry").is_visible() is True

        page.locator("#btn-distribution-retry").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)


def test_modal_traps_focus_and_closes_with_escape():
    with _central_page() as page:
        _select(page, "evento-a")
        page.locator("#btn-generate-distribution").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)

        assert page.evaluate(
            "document.getElementById('distribution-modal').contains(document.activeElement)"
        ) is True
        # Ao passar do último foco do modal, o Chromium devolve o foco ao `body`
        # (barra do navegador). O que não pode acontecer é o Tab alcançar um
        # controle da página atrás do modal.
        focus_escaped = """() => {
          const modal = document.getElementById('distribution-modal');
          const active = document.activeElement;
          return !!active && active !== document.body
            && active !== document.documentElement && !modal.contains(active);
        }"""
        for _ in range(15):
            page.keyboard.press("Tab")
            assert page.evaluate(focus_escaped) is False

        page.keyboard.press("Escape")
        page.locator("#distribution-modal").wait_for(state="hidden", timeout=5000)
        # Fechar não descarta a seleção: o operador pode revisar de novo.
        assert page.locator("#events-list .event-select:checked").count() == 1


def test_selection_survives_the_list_being_rerendered():
    with _central_page() as page:
        _select(page, "evento-b")
        page.evaluate("renderEvents(loadedEventsList)")

        assert page.locator("#event-select-evento-b").is_checked() is True
        assert page.locator("#distribution-selection-count").inner_text() == "1 evento selecionado"


def test_keyboard_navigation_selects_an_event_by_its_label():
    with _central_page() as page:
        checkbox = page.locator("#event-select-evento-a")
        checkbox.focus()
        page.keyboard.press("Space")

        assert checkbox.is_checked() is True
        assert page.locator("#distribution-selection-count").inner_text() == "1 evento selecionado"
        label = page.locator("label[for='event-select-evento-a'] input")
        assert label.get_attribute("aria-label").startswith("Selecionar o evento Barretos 2026")
