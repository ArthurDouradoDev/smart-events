"""Fase 2 — seleção múltipla e fluxo de geração no Distribution Studio.

A tela de geração **não** é a Central: ela vive em `tools/distribution_studio.html`,
é servida por outro processo, sob o prefixo `/distribution-studio`, e não é
alcançável por nenhum link da interface do produto. Os testes estruturais travam
IDs, labels e funções do fluxo no HTML do estúdio; os de Playwright exercitam o
fluxo real contra um servidor de mentira que imita os endpoints do estúdio.
"""

import json
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


REPO_ROOT = Path(__file__).parents[1]
HTML_PATH = REPO_ROOT / "tools" / "distribution_studio.html"
CENTRAL_HTML_PATH = REPO_ROOT / "server_frontend" / "index.html"

STUDIO_PATH = "/distribution-studio"
STUDIO_API = STUDIO_PATH + "/api"

PACKAGE_BYTES = b"PK\x03\x04-pacote-de-eventos-de-mentira"
FAILING_EVENT_ID = "evento-sem-cliente"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


# ── A tela é interna, e só isso ──────────────────────────────────────

def test_studio_page_lives_outside_the_distributed_frontend():
    # Nem `server_frontend/` nem `frontend/` são a casa desta tela: os dois vão
    # para dentro do bundle do PyInstaller.
    assert HTML_PATH.parent.name == "tools"
    assert not (REPO_ROOT / "server_frontend" / "distribution_studio.html").exists()


def test_central_has_no_link_to_the_studio():
    central = CENTRAL_HTML_PATH.read_text(encoding="utf-8")

    assert "distribution-studio" not in central
    assert "distribution" not in central.lower()
    # `.sepack` aparece na Central apenas como entrada (importar), nunca como
    # saída: nenhum controle de geração e nenhum link para o estúdio.
    assert "Gerar distribuição" not in central


def test_studio_page_says_it_is_internal_and_is_not_indexed():
    html = _html()

    assert 'name="robots" content="noindex, nofollow"' in html
    assert "Ferramenta interna." in html
    assert "não é distribuída com o" in html


def test_studio_is_read_only_over_the_event_data():
    """O estúdio seleciona e gera; editar cadastro de evento continua só na Central.

    A Fase 4 introduz DELETE para apagar o BINÁRIO de uma distribuição já gerada
    (histórico de auditoria), não para editar ou excluir evento/cliente/VIP.
    """
    html = _html()

    for forbidden in ("editEvent", "deleteEvent", "method: 'PUT'"):
        assert forbidden not in html
    # O único POST é a geração (revisão e job); nada grava em server_data.
    assert html.count("method: 'POST'") == 2
    # O único DELETE é o de artefato de distribuição, sob o próprio prefixo.
    assert html.count("method: 'DELETE'") == 1
    assert "/distributions/${encodeURIComponent(jobId)}/artifacts/" in html


def test_every_studio_request_stays_under_the_studio_prefix():
    html = _html()

    import re

    assert "const STUDIO_API = '/distribution-studio/api';" in html
    # Toda requisição sai pela constante do prefixo; nenhuma monta a URL na mão.
    calls = re.findall(r"fetch\(([^,)]+)", html)
    assert calls, "a tela precisa chamar a API do estúdio"
    for call in calls:
        assert "${STUDIO_API}" in call, f"chamada fora do prefixo: {call}"
    # Nenhuma chamada à API da Central sobrou no caminho.
    assert "'/api/" not in html and '"/api/' not in html


# ── Testes estruturais do fluxo ──────────────────────────────────────

def test_event_rows_expose_an_accessible_selection_checkbox():
    html = _html()

    assert 'class="event-select" id="event-select-${eventId}"' in html
    assert 'aria-label="Selecionar o evento ${escapeHTML(event.name)} para a distribuição"' in html
    assert 'onchange="toggleEventSelection(\'${eventId}\', this.checked)"' in html
    assert '<label class="event-select-label" for="event-select-${eventId}"' in html


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


def test_missing_base_shows_the_administrative_action_instead_of_building_it():
    """Fase 3: a tela nunca dispara o PyInstaller; ela diz o que precisa ser feito."""
    html = _html()

    assert "function renderDistributionBaseNote" in html
    assert 'id="distribution-base-note"' in html
    assert "caps.setup_reason" in html
    assert "caps.setup_action" in html
    # Nenhum controle da página inicia uma compilação do programa: a ação vem do
    # servidor como texto e nenhuma requisição pede um build.
    import re

    calls = re.findall(r"fetch\(([^,)]+)", html)
    assert calls
    for call in calls:
        assert "base" not in call and "build" not in call
    assert "btn-build-base" not in html


def test_ready_base_shows_its_version_date_and_the_size_warning():
    html = _html()

    assert "Build-base ${escapeHTML(caps.base_version || '?')}" in html
    assert "escapeHTML(caps.base_built_at_utc || '?')" in html
    assert "o arquivo final é grande" in html
    # As capacidades são relidas a cada revisão: um base recém-criado aparece
    # sem recarregar a página.
    assert "await loadDistributionCapabilities(true);" in html


def test_setup_job_shows_the_compiling_and_testing_steps_and_its_own_download():
    html = _html()

    assert "compiling: 'Compilando', testing: 'Testando'" in html
    assert "compiling: 'Compilando o instalador...'," in html
    assert "testing: 'Executando os testes do artefato...'," in html
    assert 'id="btn-distribution-download-setup"' in html
    assert 'id="distribution-setup-summary"' in html
    assert "<span>Build-base</span>" in html
    # O botão do instalador só existe quando o job realmente produziu um Setup.
    assert "done && distributionJobFormat === 'full_setup'" in html
    assert "downloadDistributionArtifact('setup')" in html


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


def test_selection_survives_rerender_and_is_cleared_on_download():
    html = _html()

    assert "const selectedEventIds = new Set();" in html
    assert "${selectedEventIds.has(event.id) ? 'checked' : ''}" in html
    assert "if (kind === 'download') {" in html
    # Evento que sumiu da origem não pode continuar selecionado de forma invisível.
    assert "[...selectedEventIds].forEach(id => { if (!visible.has(id)) selectedEventIds.delete(id); });" in html


def test_frontend_never_sends_a_path_to_the_distribution_endpoints():
    html = _html()

    body = html.split("await fetch(`${STUDIO_API}/jobs`, {")[1].split("});")[0]
    assert "event_ids" in body and "format" in body and "name" in body
    for forbidden in ("source", "output", "iss", "data_dir", "path"):
        assert f"{forbidden}:" not in body


# ── Histórico de distribuições (Fase 4) ───────────────────────────────

def test_history_section_has_filters_and_lists_under_the_studio_prefix():
    html = _html()

    assert "Distribuições geradas" in html
    assert 'id="history-filter-event"' in html
    assert 'id="history-filter-client"' in html
    assert 'id="history-filter-format"' in html
    assert 'id="history-filter-state"' in html
    assert 'id="history-list"' in html
    assert "function loadDistributionHistory" in html
    assert "${STUDIO_API}/distributions?" in html


def test_signature_badge_covers_every_verification_status():
    html = _html()

    for status in ("ok", "unsigned", "unknown_key", "retired_key", "invalid", "missing", "tool_unavailable"):
        assert f"{status}:" in html
    assert "Assinado e validado" in html
    assert "Não assinado — desenvolvimento" in html


def test_history_delete_requires_typed_filename_confirmation():
    """Excluir grava confirmação no próprio texto do prompt, nunca apaga direto."""
    html = _html()

    body = html.split("async function deleteHistoryArtifact")[1].split("\n    }\n")[0]
    assert "prompt(" in body
    assert "typed !== name" in body
    assert "method: 'DELETE'" in body
    # O registro do job (auditoria) não é apagado por esta ação: só o binário.
    assert "auditoria é mantido" in body


# ── Servidor de mentira para os testes de Playwright ─────────────────

_EVENTS = [
    {
        "id": "evento-a", "name": "Barretos 2026", "status": "ACTIVE",
        "start_time": "2026-08-20T10:00:00Z", "end_time": "2026-08-25T22:00:00Z",
        "client": "Vivo", "region": "SP", "sites": 1, "cells": 1,
    },
    {
        "id": "evento-b", "name": "Rock 2026", "status": "SCHEDULED",
        "start_time": "2026-09-20T10:00:00Z", "end_time": "2026-09-25T22:00:00Z",
        "client": "TIM", "region": "RJ", "sites": 1, "cells": 1,
    },
    {
        "id": FAILING_EVENT_ID, "name": "Evento sem cliente", "status": "SCHEDULED",
        "start_time": "2026-10-01T10:00:00Z", "end_time": "2026-10-02T22:00:00Z",
        "client": "Claro", "region": "MG", "sites": 0, "cells": 0,
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

_HISTORY_ROWS = [
    {
        "job_id": "a" * 32, "format": "event_package", "state": "ready",
        "name": "Barretos 2026", "event_ids": ["evento-a"], "clients": ["Vivo"],
        "vip_policy": "auto", "created_at_utc": "2026-08-31T12:00:00Z",
        "finished_at_utc": "2026-08-31T12:00:05Z",
        "generator": {"user": "operador", "host": "estudio-01"},
        "source": {"app_version": "1.0.0", "commit": "abcdef1234567890", "dirty": False},
        "signature_status": "unsigned", "signature_key_id": "",
        "package_sha256": "b" * 64, "package_bytes": 4096,
        "artifacts_available": {"package": True, "manifest": True},
        "durations_seconds": [], "errors": [], "warnings": [],
    },
]


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
            "client": event["client"], "region": event["region"],
            "sites": event["sites"], "cells": 1, "clusters": 0, "pm_tasks": 1,
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


class _StudioHandler(SimpleHTTPRequestHandler):
    """Imita os endpoints do estúdio. O job vira `ready` (ou `failed`) na 2ª consulta."""

    polls: dict = {}
    deleted: list = []

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
        if clean == STUDIO_PATH:
            body = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if clean == STUDIO_API + "/events":
            self._json(_EVENTS)
            return
        if clean == STUDIO_API + "/capabilities":
            self._json(_CAPABILITIES)
            return
        if clean == STUDIO_API + "/distributions":
            self._json(_HISTORY_ROWS)
            return
        if clean.startswith(STUDIO_API + "/jobs/") and clean.endswith("/download"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="pacote.sepack"')
            self.send_header("Content-Length", str(len(PACKAGE_BYTES)))
            self.end_headers()
            self.wfile.write(PACKAGE_BYTES)
            return
        if clean.startswith(STUDIO_API + "/jobs/"):
            job_id = clean.rsplit("/", 1)[-1]
            if job_id == _HISTORY_ROWS[0]["job_id"]:
                self._json(self._job(
                    job_id, "ready",
                    artifacts={"package": {
                        "name": "pacote.sepack", "bytes": 4096, "sha256": "b" * 64,
                        "media_type": "application/octet-stream",
                    }},
                ))
                return
            seen = _StudioHandler.polls.get(job_id, 0) + 1
            _StudioHandler.polls[job_id] = seen
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
        # Fora do prefixo do estúdio não existe nada — inclusive a raiz.
        self._json({"detail": "not found"}, 404)

    def do_POST(self):
        clean = self.path.split("?", 1)[0]
        if clean == STUDIO_API + "/preview":
            self._json(_preview_for(self._read_json().get("event_ids") or []))
            return
        if clean == STUDIO_API + "/jobs":
            payload = self._read_json()
            prefix = "fail" if FAILING_EVENT_ID in (payload.get("event_ids") or []) else "ok"
            self._json(self._job(f"{prefix}{'0' * 28}", "queued"))
            return
        self._json({"detail": "not found"}, 404)

    def do_DELETE(self):
        clean = self.path.split("?", 1)[0]
        if clean.startswith(STUDIO_API + "/distributions/") and "/artifacts/" in clean:
            _StudioHandler.deleted.append(clean)
            self._json({**_HISTORY_ROWS[0], "artifacts_available": {"package": False, "manifest": True}})
            return
        self._json({"detail": "not found"}, 404)


@contextmanager
def _studio_server():
    _StudioHandler.polls = {}
    _StudioHandler.deleted = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StudioHandler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


@contextmanager
def _studio_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _studio_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url + STUDIO_PATH, wait_until="domcontentloaded")
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

def test_selecting_an_event_enables_generation():
    with _studio_page() as page:
        _select(page, "evento-a")

        assert page.locator("#distribution-selection-count").inner_text() == "1 evento selecionado"
        assert page.locator("#btn-generate-distribution").is_disabled() is False


def test_select_all_visible_marks_every_row_and_clearing_resets_it():
    with _studio_page() as page:
        page.locator("#select-all-visible-events").check()

        assert page.locator("#studio-events-list .event-select:checked").count() == len(_EVENTS)
        assert "3 eventos selecionados" in page.locator("#distribution-selection-count").inner_text()

        page.locator("#btn-clear-event-selection").click()
        assert page.locator("#studio-events-list .event-select:checked").count() == 0
        assert page.locator("#btn-generate-distribution").is_disabled() is True


def test_review_modal_shows_names_counts_and_the_legacy_vip_warning():
    with _studio_page() as page:
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
    with _studio_page() as page:
        _select(page, FAILING_EVENT_ID)
        page.locator("#btn-generate-distribution").click()
        page.locator("#distribution-preview-content").wait_for(state="visible", timeout=5000)

        assert "reference.client_missing" in page.locator("#distribution-preview-content").inner_text()
        assert page.locator("#btn-distribution-generate").is_disabled() is True


def test_job_progress_reaches_ready_and_downloads_the_package():
    with _studio_page() as page:
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
        assert page.locator("#studio-events-list .event-select:checked").count() == 0


def test_failed_job_shows_the_step_diagnostic_and_retry_button():
    with _studio_page() as page:
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
    with _studio_page() as page:
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
        assert page.locator("#studio-events-list .event-select:checked").count() == 1


def test_selection_survives_the_list_being_rerendered():
    with _studio_page() as page:
        _select(page, "evento-b")
        page.evaluate("renderStudioEvents(loadedEventsList)")

        assert page.locator("#event-select-evento-b").is_checked() is True
        assert page.locator("#distribution-selection-count").inner_text() == "1 evento selecionado"


def test_history_lists_rows_and_deletes_artifact_after_typed_confirmation():
    with _studio_page() as page:
        page.locator("#history-list .history-row").first.wait_for(timeout=5000)
        row = page.locator("#history-list .history-row").first
        assert "Barretos 2026" in row.inner_text()
        assert "Não assinado — desenvolvimento" in row.inner_text()
        assert "operador" in row.inner_text()

        page.on("dialog", lambda dialog: dialog.accept("pacote.sepack"))
        page.locator("#history-list button", has_text="Excluir").click()
        # A exclusão faz dois round-trips assíncronos (ler o job, depois apagar);
        # espera o servidor de mentira realmente registrar a chamada DELETE.
        for _ in range(50):
            if _StudioHandler.deleted:
                break
            page.wait_for_timeout(50)

        assert len(_StudioHandler.deleted) == 1
        assert "/artifacts/package" in _StudioHandler.deleted[0]


def test_keyboard_navigation_selects_an_event_by_its_label():
    with _studio_page() as page:
        checkbox = page.locator("#event-select-evento-a")
        checkbox.focus()
        page.keyboard.press("Space")

        assert checkbox.is_checked() is True
        assert page.locator("#distribution-selection-count").inner_text() == "1 evento selecionado"
        label = page.locator("label[for='event-select-evento-a'] input")
        assert label.get_attribute("aria-label").startswith("Selecionar o evento Barretos 2026")
