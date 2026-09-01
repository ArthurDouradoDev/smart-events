"""Fase 2 — serviço e endpoints do Distribution Studio.

A geração de distribuições é uma **ferramenta de repositório**, não um recurso do
produto: ela vive em `tools/distribution_studio.py`, num processo e numa porta
próprios. O `server.py` da Central não conhece nada disto — o último teste deste
arquivo trava exatamente isso.

Os testes chamam as funções dos endpoints diretamente (sem subir HTTP): o projeto
não tem `httpx` instalado e o TestClient do FastAPI depende dele. Todo estado vive
em diretórios temporários; nenhum teste toca o `server_data` do workspace.
"""

import json
import threading
import time
from pathlib import Path

import pytest

import server
from core import distribution_service as ds
from core import event_package as ep
from tools import distribution_studio as studio


# ── Construtores de dados ────────────────────────────────────────────

def _event(event_id, name, client, region="SP"):
    return {
        "id": event_id,
        "name": name,
        "status": "ACTIVE",
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{
            "id": "SITE-1",
            "cells": [
                {"id": "CELL-1", "azimuth": 0, "beamwidth": 120},
                {"id": "CELL-2", "azimuth": 120, "beamwidth": 120},
            ],
        }],
        "clusters": [{"id": "sul", "name": "Sul", "members": []}],
        "oss": {"cliente": client, "region": region, "base_url": "", "import_folder": ""},
        "integration": {"pm_tasks": [{"tech": "4G", "task_id": 2279}]},
    }


def _client(client_id, name, regions=(("SP", "https://10.0.0.1:31943"),), logo=""):
    return {
        "id": client_id,
        "name": name,
        "logo": logo,
        "regionais": [{"region": region, "ip": ip} for region, ip in regions],
    }


def _vip(vip_id, name, client, region="SP"):
    return {"id": vip_id, "name": name, "cliente": client, "oss": region, "task_id": 2073}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _server_data(root: Path, events=(), clientes=(), vips=(), logos=()) -> Path:
    for name in ("events", "clientes", "vips", "logos"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for event in events:
        _write_json(root / "events" / f"{event['id']}.json", event)
    for client in clientes:
        _write_json(root / "clientes" / f"{client['id']}.json", client)
    for vip in vips:
        _write_json(root / "vips" / f"{vip['id']}.json", vip)
    for logo_name, data in logos:
        (root / "logos" / logo_name).write_bytes(data)
    return root


@pytest.fixture
def source(tmp_path):
    """Dois eventos de clientes diferentes, um VIP explícito e um legado."""
    return _server_data(
        tmp_path / "server_data",
        events=[
            _event("barretos-2026", "Barretos 2026", "Vivo"),
            _event("rock-2026", "Rock 2026", "TIM", region="RJ"),
        ],
        clientes=[
            _client("vivo", "Vivo", logo="vivo.png"),
            _client("tim", "TIM", regions=(("RJ", "https://10.0.0.2:31943"),)),
        ],
        vips=[
            _vip("vip-vivo", "Diretor Vivo", "Vivo"),
            _vip("vip-tim", "Diretor TIM", "TIM", region="RJ"),
        ],
        logos=[("vivo.png", b"\x89PNG\r\n\x1a\n-logo-vivo")],
    )


@pytest.fixture
def service(tmp_path, source):
    return ds.DistributionService(root=tmp_path / "distributions", source_dir=source)


@pytest.fixture
def api(monkeypatch, service):
    """Endpoints do estúdio apontados para o serviço temporário."""
    monkeypatch.setattr(studio, "DISTRIBUTION_SERVICE", service)
    return service


def _http_error(call, *args, **kwargs):
    with pytest.raises(studio.HTTPException) as excinfo:
        call(*args, **kwargs)
    return excinfo.value


# ── Lista de eventos ─────────────────────────────────────────────────

def test_event_list_summarizes_without_shipping_the_whole_event(api, source):
    rows = studio.get_studio_events()

    assert [row["id"] for row in rows] == ["barretos-2026", "rock-2026"]
    barretos = rows[0]
    assert barretos["name"] == "Barretos 2026"
    assert barretos["client"] == "Vivo"
    assert barretos["region"] == "SP"
    assert barretos["sites"] == 1
    assert barretos["cells"] == 2
    # A tela desenha uma caixa de seleção: polígono, células e tasks nao precisam
    # atravessar a rede para isso.
    assert set(barretos) == {
        "id", "name", "status", "start_time", "end_time",
        "client", "region", "sites", "cells",
    }


def test_event_list_skips_unreadable_files_instead_of_failing(api, source):
    (source / "events" / "quebrado.json").write_text("{ nao e json", encoding="utf-8")

    assert [row["id"] for row in studio.get_studio_events()] == [
        "barretos-2026", "rock-2026",
    ]


# ── Capacidades ──────────────────────────────────────────────────────

def test_capabilities_lists_event_package_only_in_phase2(api):
    capabilities = studio.get_distribution_capabilities()

    assert capabilities["formats"] == ["event_package"]
    formats = {item["id"]: item for item in capabilities["available_formats"]}
    assert formats["event_package"]["enabled"] is True
    # O Setup completo é oferecido como opção visível e desabilitada, com o motivo.
    assert formats["full_setup"]["enabled"] is False
    assert "build-base" in formats["full_setup"]["reason"]
    assert capabilities["max_events"] == ds.MAX_EVENTS_PER_JOB


# ── Revisão ──────────────────────────────────────────────────────────

def test_preview_returns_dependencies_counts_warnings_and_errors(api):
    preview = studio.post_distribution_preview(
        {"event_ids": ["barretos-2026", "rock-2026"], "name": "Dois clientes"}
    )

    assert preview["ok"] is True
    assert preview["event_ids"] == ["barretos-2026", "rock-2026"]
    assert preview["clients"] == ["TIM", "Vivo"]
    assert preview["counts"] == {
        "events": 2, "clientes": 2, "vips": 2, "logos": 1,
        "regions": 2, "sites": 2, "cells": 4, "clusters": 2, "pm_tasks": 2,
    }

    # Detalhe por evento: a revisão precisa comparar com os cards da lista.
    rows = {row["id"]: row for row in preview["events"]}
    assert rows["barretos-2026"]["client"] == "Vivo"
    assert rows["barretos-2026"]["region"] == "SP"
    assert rows["barretos-2026"]["sites"] == 1
    assert rows["barretos-2026"]["cells"] == 2
    assert rows["barretos-2026"]["clusters"] == 1
    assert rows["barretos-2026"]["pm_tasks"] == 1

    clientes = {row["id"]: row for row in preview["clientes"]}
    assert clientes["tim"]["regions"] == ["RJ"]

    # Sem associação explícita, os VIPs entram por fallback legado — e isso aparece.
    assert {row["id"]: row["source"] for row in preview["vips"]} == {
        "vip-vivo": "fallback", "vip-tim": "fallback",
    }
    assert preview["fallback_vip_ids"] == ["vip-tim", "vip-vivo"]
    assert any("allback" in message for message in preview["warnings"])

    # O que nunca entra no pacote é parte do contrato da revisão.
    assert "credentials.json" in preview["excluded"]
    assert "conciliados pelo id" in preview["reconciliation_note"]
    assert preview["suggested_filename"].endswith(".sepack")
    # O caminho do server_data do operador não é exposto para o navegador.
    assert "source_dir" not in preview


def test_preview_reports_blocking_errors_without_creating_anything(api, source, tmp_path):
    # Evento sem cliente cadastrado: erro bloqueante, e nenhum artefato criado.
    _write_json(source / "events" / "orfao.json", _event("orfao", "Órfão", "Claro"))

    preview = studio.post_distribution_preview({"event_ids": ["orfao"]})

    assert preview["ok"] is False
    assert "reference.client_missing" in [item["code"] for item in preview["errors"]]
    assert not (tmp_path / "distributions").exists()


def test_preview_rejects_unknown_event_id(api):
    error = _http_error(studio.post_distribution_preview, {"event_ids": ["nao-existe"]})

    assert error.status_code == 400
    assert error.detail["code"] == "event.unknown"
    assert "nao-existe" in error.detail["message"]


def test_preview_rejects_unknown_vip_policy_and_empty_selection(api):
    empty = _http_error(studio.post_distribution_preview, {"event_ids": []})
    assert empty.status_code == 400
    assert empty.detail["code"] == "selection.empty"

    policy = _http_error(
        studio.post_distribution_preview,
        {"event_ids": ["barretos-2026"], "vip_policy": "todos"},
    )
    assert policy.status_code == 400
    assert policy.detail["code"] == "argument.invalid"


def test_selection_larger_than_the_limit_is_rejected(api, service, source):
    for index in range(ds.MAX_EVENTS_PER_JOB + 1):
        event_id = f"evento-{index:03d}"
        _write_json(source / "events" / f"{event_id}.json", _event(event_id, event_id, "Vivo"))

    ids = sorted(service.known_event_ids())
    error = _http_error(studio.post_distribution_preview, {"event_ids": ids})

    assert error.status_code == 400
    assert error.detail["code"] == "selection.too_large"


# ── Criação de job ───────────────────────────────────────────────────

def test_create_job_uses_only_server_selected_paths(api, service, tmp_path):
    hostile = {
        "event_ids": ["barretos-2026"],
        "source": str(tmp_path / "outra-origem"),
        "output": "C:/Windows/Temp/saida.sepack",
        "iss": "installer/SmartEvents.iss",
    }

    error = _http_error(studio.post_distribution, hostile)
    assert error.status_code == 400
    assert "source" in error.detail and "output" in error.detail

    # O caminho aceito é sempre o do próprio servidor.
    record = studio.post_distribution({"event_ids": ["barretos-2026"]})
    job = service.wait_for(record["job_id"])
    artifact, _entry = service.artifact(job["job_id"], "package")
    assert artifact.parent == service.root / job["job_id"]
    assert not (tmp_path / "outra-origem").exists()


def test_create_job_rejects_the_setup_format_until_phase3(api):
    error = _http_error(
        studio.post_distribution, {"event_ids": ["barretos-2026"], "format": "full_setup"}
    )

    assert error.status_code == 400
    assert error.detail["code"] == "format.unsupported"
    assert "build-base" in error.detail["message"]


def test_job_transitions_to_ready_and_downloads_registered_file(api, service):
    record = studio.post_distribution(
        {"event_ids": ["barretos-2026", "rock-2026"], "name": "Dois clientes"}
    )
    assert record["state"] == "queued"

    service.wait_for(record["job_id"])
    job = studio.get_distribution(record["job_id"])

    assert job["state"] == "ready"
    assert [step["state"] for step in job["steps"]] == [
        "queued", "validating", "packaging", "ready",
    ]
    assert job["counts"]["events"] == 2
    assert job["manifest"]["event_ids"] == ["barretos-2026", "rock-2026"]

    package = job["artifacts"]["package"]
    assert package["name"].endswith(".sepack")
    assert package["bytes"] > 0
    assert len(package["sha256"]) == 64

    response = studio.download_distribution(record["job_id"])
    assert Path(response.path).name == package["name"]
    assert response.filename == package["name"]
    assert ep.sha256_of(response.path) == package["sha256"]

    manifest_response = studio.download_distribution_manifest(record["job_id"])
    assert json.loads(Path(manifest_response.path).read_text(encoding="utf-8")) == job["manifest"]

    # O pacote baixado é um .sepack válido e importável.
    inspection = ep.inspect_package(response.path)
    assert inspection["ok"] is True
    assert inspection["counts"]["events"] == 2


def test_failed_job_keeps_redacted_diagnostic(api, service, monkeypatch):
    leaked = r"C:\Users\operador\AppData\Local\SmartEvents\staging\payload.zip"

    def _boom(*_args, **_kwargs):
        raise OSError(f"disco cheio ao gravar {leaked} (token=abc123)")

    monkeypatch.setattr(ds.ep, "build_package", _boom)
    record = studio.post_distribution({"event_ids": ["barretos-2026"]})
    service.wait_for(record["job_id"])
    job = studio.get_distribution(record["job_id"])

    assert job["state"] == "failed"
    assert job["failed_step"] == "packaging"
    assert job["diagnostic"]
    assert job["errors"] and job["errors"][0]["code"] == "job.failed"

    exposed = json.dumps(job, ensure_ascii=False)
    assert "operador" not in exposed
    assert "AppData" not in exposed
    assert "abc123" not in exposed
    # O nome do arquivo sobrevive: é ele que ajuda a diagnosticar.
    assert "payload.zip" in job["diagnostic"]


def test_failed_job_can_be_retried_after_the_cause_is_gone(api, service, source):
    _write_json(source / "events" / "orfao.json", _event("orfao", "Órfão", "Claro"))
    failed = service.wait_for(studio.post_distribution({"event_ids": ["orfao"]})["job_id"])
    assert failed["state"] == "failed"
    assert failed["failed_step"] == "validating"
    assert "reference.client_missing" in [item["code"] for item in failed["errors"]]

    _write_json(source / "clientes" / "claro.json", _client("claro", "Claro"))
    retried = service.wait_for(studio.post_distribution({"event_ids": ["orfao"]})["job_id"])

    assert retried["state"] == "ready"
    assert retried["job_id"] != failed["job_id"]


# ── Download ─────────────────────────────────────────────────────────

def test_download_rejects_unready_or_unknown_job(api, service, monkeypatch):
    unknown = _http_error(studio.download_distribution, "0" * 32)
    assert unknown.status_code == 404
    assert unknown.detail["code"] == "job.unknown"

    # Um job_id fora do formato nunca chega a compor um caminho.
    for hostile in ("../../server_data", "..", "a" * 31, "nao-e-hex" + "0" * 23):
        invalid = _http_error(studio.download_distribution, hostile)
        assert invalid.status_code == 400
        assert invalid.detail["code"] == "job.invalid_id"

    # Job existente mas ainda não pronto: download bloqueado.
    blocked = ds.DistributionService(root=service.root, source_dir=service.source_dir)
    monkeypatch.setattr(blocked, "_run_job", lambda _job_id: None)
    queued = blocked.create_job(["barretos-2026"], background=False)
    not_ready = _http_error(studio.download_distribution, queued["job_id"])
    assert not_ready.status_code == 409
    assert not_ready.detail["code"] == "job.not_ready"


def test_download_rejects_artifact_removed_from_disk(api, service):
    job = service.wait_for(studio.post_distribution({"event_ids": ["barretos-2026"]})["job_id"])
    artifact, _entry = service.artifact(job["job_id"], "package")
    artifact.unlink()

    missing = _http_error(studio.download_distribution, job["job_id"])
    assert missing.status_code == 410
    assert missing.detail["code"] == "artifact.missing"


# ── Concorrência e recuperação ───────────────────────────────────────

def test_concurrent_writers_are_serialized(api, service, monkeypatch):
    records = []
    lock = threading.Lock()
    # Conta quantos jobs estão dentro da etapa de escrita ao mesmo tempo. Comparar
    # timestamps não serviria: eles têm resolução de segundo e os jobs duram milissegundos.
    inside = 0
    peak = 0
    real_build = ds.ep.build_package

    def _counted_build(*args, **kwargs):
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        try:
            time.sleep(0.05)
            return real_build(*args, **kwargs)
        finally:
            with lock:
                inside -= 1

    monkeypatch.setattr(ds.ep, "build_package", _counted_build)

    def _create(event_id):
        record = studio.post_distribution({"event_ids": [event_id], "name": event_id})
        with lock:
            records.append(record)

    threads = [
        threading.Thread(target=_create, args=(event_id,))
        for event_id in ("barretos-2026", "rock-2026", "barretos-2026")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(records) == 3
    jobs = [service.wait_for(record["job_id"]) for record in records]
    assert {job["state"] for job in jobs} == {"ready"}

    # Cada job tem a própria pasta e o próprio artefato; nada se mistura.
    job_ids = {job["job_id"] for job in jobs}
    assert len(job_ids) == 3
    for job in jobs:
        path, entry = service.artifact(job["job_id"], "package")
        assert path.parent.name == job["job_id"]
        assert ep.sha256_of(path) == entry["sha256"]
        manifest = ep.inspect_package(path)["manifest"]
        assert list(manifest["event_ids"]) == job["event_ids"]

    # A trava do lock de escrita: nunca dois jobs empacotando ao mesmo tempo.
    assert peak == 1


def test_job_never_stays_stuck_when_its_record_cannot_be_read(api, service, monkeypatch):
    # Uma leitura que falha (no Windows, o `os.replace` concorrente) não pode deixar
    # o job preso em `queued`: a interface consultaria para sempre, sem erro.
    original = ds.DistributionService._load
    broken = {"worker": False}

    def _flaky(self, job_id):
        # Só a primeira leitura feita pelo worker falha; o polling continua normal.
        if threading.current_thread().name.startswith("distribution-") and not broken["worker"]:
            broken["worker"] = True
            raise ds.DistributionError(
                "job.unreadable", r"Permission denied: C:\Users\ana\jobs\x.json", 500
            )
        return original(self, job_id)

    monkeypatch.setattr(ds.DistributionService, "_load", _flaky)
    job = service.wait_for(
        studio.post_distribution({"event_ids": ["rock-2026"]})["job_id"], timeout=10
    )

    assert job["state"] == "failed"
    assert job["errors"][0]["code"] == "job.unreadable"
    assert "ana" not in json.dumps(job)


def test_restart_recovers_completed_job_metadata(api, service, tmp_path, source):
    job = service.wait_for(studio.post_distribution({"event_ids": ["rock-2026"]})["job_id"])

    # Uma instância nova (página recarregada, servidor reiniciado) lê o mesmo estado.
    restarted = ds.DistributionService(root=tmp_path / "distributions", source_dir=source)
    recovered = restarted.get_job(job["job_id"])

    assert recovered["state"] == "ready"
    assert recovered["artifacts"] == job["artifacts"]
    assert recovered["manifest"] == job["manifest"]
    path, entry = restarted.artifact(job["job_id"], "package")
    assert ep.sha256_of(path) == entry["sha256"]
    assert [item["job_id"] for item in restarted.list_jobs()] == [job["job_id"]]


def test_cleanup_removes_stale_incomplete_jobs_and_never_a_ready_one(api, service, monkeypatch):
    ready = service.wait_for(studio.post_distribution({"event_ids": ["rock-2026"]})["job_id"])

    stale = ds.DistributionService(root=service.root, source_dir=service.source_dir)
    monkeypatch.setattr(stale, "_run_job", lambda _job_id: None)
    abandoned = stale.create_job(["barretos-2026"], background=False)["job_id"]

    assert service.cleanup_incomplete(max_age_hours=0) == [abandoned]
    assert service.get_job(ready["job_id"])["state"] == "ready"
    with pytest.raises(ds.DistributionError):
        service.get_job(abandoned)


# ── Redação ──────────────────────────────────────────────────────────

def test_redact_removes_operator_paths_and_secret_values():
    assert ds.redact(r"falha em C:\Users\ana\SmartEvents\dados.json") == (
        "falha em <caminho>/dados.json"
    )
    assert ds.redact("/home/ana/.config/session.json") == "<caminho>/session.json"
    assert ds.redact("SMARTEVENTS_TOKEN=abc-123 recusado") == "SMARTEVENTS_TOKEN=<oculto> recusado"
    assert ds.redact("senha: hunter2") == "senha=<oculto>"
    # Rota de API não é caminho de usuário e continua legível.
    assert (
        ds.redact("erro em /distribution-studio/api/preview")
        == "erro em /distribution-studio/api/preview"
    )


# ── A Central nao gera distribuicao ──────────────────────────────────

def test_central_server_exposes_no_distribution_endpoint():
    """O `server.py` voltou ao que era antes da Fase 2.

    A Central é o que o usuário final recebe; gerar artefato é operação de quem
    distribui. Nenhum endpoint, serviço ou import de distribuição pode sobreviver
    aqui — se sobrevivesse, entraria no bundle junto com `server` (que é
    `hiddenimport` do `main.spec`).
    """
    for name in (
        "DISTRIBUTION_SERVICE", "DistributionService", "DistributionError",
        "get_distribution_capabilities", "post_distribution_preview",
        "post_distribution", "get_distribution",
        "download_distribution", "download_distribution_manifest",
    ):
        assert not hasattr(server, name), f"server.py ainda expõe {name}"

    paths = {getattr(route, "path", "") for route in server.app.routes}
    assert [path for path in paths if "distribution" in path] == []

    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "distribution" not in source.lower()
    # A Central *importa* o resultado — é o que o usuário final recebe e a razão
    # de `core.event_package` ser embarcado. O que ela não pode ter é o gerador:
    # é ele que arrastaria a operação de quem distribui para dentro do bundle.
    for builder in ("distribution_service", "build_package", "preview_package", "suggested_filename"):
        assert builder not in source, f"server.py ainda alcança o gerador ({builder})"


def test_central_frontend_has_no_selection_or_generation_ui():
    html = (Path(__file__).parents[1] / "server_frontend" / "index.html").read_text(
        encoding="utf-8"
    )

    for marker in (
        "distribution", "event-select", "Gerar distribuição",
        "Selecionar todos visíveis",
    ):
        assert marker.lower() not in html.lower(), f"a Central ainda tem {marker!r}"


def test_central_frontend_imports_packages_without_generating_them():
    """A Central recebe o `.sepack`; quem o monta é o estúdio."""
    html = (Path(__file__).parents[1] / "server_frontend" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="btn-importar-pacote"' in html
    assert 'accept=".sepack"' in html
    assert "/api/events/import/preview" in html
    # Revisão antes de gravar e política fixa: substituir só pela linha de comando.
    assert "confirmPackageImport" in html
    assert "conflict" not in html.lower()


def test_studio_never_enters_the_distributed_executable():
    """O estúdio fica no repositório; o `.exe` leva só o resultado dele."""
    spec = (Path(__file__).parents[1] / "main.spec").read_text(encoding="utf-8")

    assert "distribution_studio" not in spec
    assert "distribution_service" not in spec
    # `tools/` não é empacotado como dado nem como pacote.
    assert "'tools'" not in spec and '"tools"' not in spec
    # O importador de pacote continua embarcado: quem recebe o .sepack precisa dele.
    assert "core.event_package" in spec


def test_studio_serves_nothing_outside_its_own_prefix():
    """Sem o link não há tela: `/` não existe no app do estúdio."""
    paths = {getattr(route, "path", "") for route in studio.app.routes}

    assert studio.STUDIO_PATH in paths
    assert "/" not in paths
    for path in paths:
        assert path.startswith(studio.STUDIO_PATH), f"rota fora do prefixo: {path}"
