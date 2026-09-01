"""Fase 4 — histórico e auditoria de distribuições geradas pelo Distribution Studio.

O histórico é o próprio inventário de jobs (`DistributionService.list_jobs`),
resumido e filtrável; nenhum estado novo é criado. Excluir um artefato remove
só o binário — o registro (quem gerou, de onde, hash e status de assinatura)
nunca é apagado, conforme a decisão de retenção do plano.

Segue o mesmo padrão de `tests/test_distribution_studio_api.py`: chama as
funções dos endpoints diretamente (sem HTTP) e nunca toca o `server_data` do
workspace.
"""

import json
from pathlib import Path

import pytest

from core import distribution_service as ds
from tools import distribution_studio as studio


def _event(event_id, name, client, region="SP"):
    return {
        "id": event_id, "name": name, "status": "ACTIVE",
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{"id": "SITE-1", "cells": [{"id": "CELL-1", "azimuth": 0, "beamwidth": 120}]}],
        "oss": {"cliente": client, "region": region, "base_url": "", "import_folder": ""},
        "integration": {"pm_tasks": [{"tech": "4G", "task_id": 2279}]},
    }


def _client(client_id, name, regions=(("SP", "https://10.0.0.1:31943"),)):
    return {
        "id": client_id, "name": name, "logo": "",
        "regionais": [{"region": region, "ip": ip} for region, ip in regions],
    }


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "server_data"
    for name in ("events", "clientes", "vips", "logos"):
        (root / name).mkdir(parents=True, exist_ok=True)
    _write_json(root / "events" / "barretos-2026.json", _event("barretos-2026", "Barretos 2026", "Vivo"))
    _write_json(root / "events" / "rock-2026.json", _event("rock-2026", "Rock 2026", "TIM", region="RJ"))
    _write_json(root / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    _write_json(
        root / "clientes" / "tim.json", _client("tim", "TIM", regions=(("RJ", "https://10.0.0.2:31943"),))
    )
    return root


@pytest.fixture
def service(tmp_path, source, monkeypatch):
    # Nenhuma chave de assinatura configurada: os pacotes destes testes saem
    # sem assinatura, como qualquer instalação anterior à Fase 4.
    monkeypatch.delenv(ds.psig.ENV_SIGNING_KEY_PATH, raising=False)
    return ds.DistributionService(
        root=tmp_path / "distributions", source_dir=source, dist_dir=tmp_path / "sem-build-base",
    )


@pytest.fixture
def api(monkeypatch, service):
    monkeypatch.setattr(studio, "DISTRIBUTION_SERVICE", service)
    return service


def _ready_job(service, event_ids, *, name=None):
    record = service.create_job(event_ids, name=name)
    return service.wait_for(record["job_id"])


# ── Histórico ──────────────────────────────────────────────────────────

def test_audit_history_records_generator_source_and_signature_status(api, service, monkeypatch):
    monkeypatch.setenv("SMARTEVENTS_GENERATOR_USER", "ana.operadora")
    job = _ready_job(service, ["barretos-2026"], name="Barretos")

    rows = service.audit_rows()

    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == job["job_id"]
    assert row["name"] == "Barretos"
    assert row["event_ids"] == ["barretos-2026"]
    assert row["clients"] == ["Vivo"]
    assert row["generator"]["user"] == "ana.operadora"
    assert "app_version" in row["source"]
    # Sem chave de assinatura configurada, o pacote sai não assinado — e o
    # histórico precisa dizer isso, não silenciar o campo.
    assert row["signature_status"] == "unsigned"
    assert row["package_sha256"] and len(row["package_sha256"]) == 64
    assert row["artifacts_available"]["package"] is True


def test_audit_history_never_disappears_after_cleanup_of_incomplete_jobs(api, service):
    ready = _ready_job(service, ["barretos-2026"])
    service.cleanup_incomplete(max_age_hours=0)

    rows = service.audit_rows()
    assert [row["job_id"] for row in rows] == [ready["job_id"]]


def test_audit_history_filters_by_event_client_format_and_state(api, service):
    barretos = _ready_job(service, ["barretos-2026"])
    rock = _ready_job(service, ["rock-2026"])

    assert [r["job_id"] for r in service.audit_rows(event_id="barretos-2026")] == [barretos["job_id"]]
    assert [r["job_id"] for r in service.audit_rows(client="TIM")] == [rock["job_id"]]
    assert {r["job_id"] for r in service.audit_rows(fmt="event_package")} == {
        barretos["job_id"], rock["job_id"],
    }
    assert service.audit_rows(fmt="full_setup") == []
    assert service.audit_rows(state="failed") == []
    assert len(service.audit_rows(state="ready")) == 2


def test_studio_history_endpoint_applies_query_filters(api, service):
    _ready_job(service, ["barretos-2026"])
    _ready_job(service, ["rock-2026"])

    rows = studio.get_distribution_history(client="Vivo")

    assert len(rows) == 1
    assert rows[0]["clients"] == ["Vivo"]


# ── Exclusão de artefato (preserva o registro) ─────────────────────────

def test_delete_artifact_removes_binary_and_preserves_audit_record(api, service):
    job = _ready_job(service, ["barretos-2026"])
    package_name = job["artifacts"]["package"]["name"]
    package_path = service.job_dir(job["job_id"]) / package_name
    assert package_path.is_file()

    row = service.delete_artifact(job["job_id"], "package", confirm_name=package_name)

    assert not package_path.is_file()
    assert row["artifacts_available"]["package"] is False
    # O manifesto (auditoria) continua no disco e no histórico.
    assert row["artifacts_available"]["manifest"] is True
    rows_after = service.audit_rows()
    assert len(rows_after) == 1
    assert rows_after[0]["job_id"] == job["job_id"]
    # Download volta a não achar o binário apagado (410), sem esconder o job.
    with pytest.raises(ds.DistributionError) as excinfo:
        service.artifact(job["job_id"], "package")
    assert excinfo.value.code == "artifact.missing"


def test_delete_artifact_requires_exact_filename_confirmation(api, service):
    job = _ready_job(service, ["barretos-2026"])
    package_name = job["artifacts"]["package"]["name"]
    package_path = service.job_dir(job["job_id"]) / package_name

    with pytest.raises(ds.DistributionError) as excinfo:
        service.delete_artifact(job["job_id"], "package", confirm_name="nome-errado.sepack")

    assert excinfo.value.code == "artifact.confirmation_mismatch"
    assert package_path.is_file()  # nada foi apagado


def test_studio_delete_endpoint_rejects_wrong_confirmation(api, service):
    job = _ready_job(service, ["barretos-2026"])

    error = None
    try:
        studio.delete_distribution_artifact(job["job_id"], "package", confirm_name="errado.sepack")
    except studio.HTTPException as exc:
        error = exc
    assert error is not None
    assert error.status_code == 400


def test_delete_artifact_rejects_unsafe_kind_or_job_id(api, service):
    job = _ready_job(service, ["barretos-2026"])

    with pytest.raises(ds.DistributionError):
        service.delete_artifact(job["job_id"], "../etc", confirm_name="x")
    with pytest.raises(ds.DistributionError):
        service.delete_artifact("../not-a-job-id", "package", confirm_name="x")


# ── Auditoria não vaza segredo ou ambiente completo ─────────────────────

def test_audit_row_never_leaks_operator_paths_or_secrets(api, service, monkeypatch):
    monkeypatch.setenv("SMARTEVENTS_GENERATOR_USER", "ana.operadora")
    job = _ready_job(service, ["barretos-2026"])
    # Simula erro com caminho de usuário e segredo, como um job real poderia gerar.
    service._fail(  # noqa: SLF001 - testa exatamente a passagem por redact()
        job["job_id"], "packaging",
        errors=[{"code": "job.failed", "path": "",
                 "message": r"Falha em C:\Users\ana\segredo\dados.json TOKEN=abc123"}],
        diagnostic=r"C:\Users\ana\segredo\dados.json TOKEN=abc123",
    )

    row = service.audit_rows()[0]
    serialized = json.dumps(row)
    assert "ana\\segredo" not in serialized
    assert "TOKEN=abc123" not in serialized
    assert "<oculta>" not in json.dumps(row["generator"])  # identidade não é redigida, é dado simples
