"""Importação de pacotes `.sepack` pela interface da Smart Events Central.

Gerar um pacote é operação de quem distribui e vive fora do produto; importar é
função da Central, que é justamente o que o usuário final recebe. Estes testes
travam esse recorte: revisão antes de gravar, política `preserve` fixa e nenhum
caminho vindo do navegador.

Como em `test_distribution_studio_api.py`, os endpoints são chamados direto
(sem subir HTTP): o projeto não tem `httpx` e o TestClient depende dele. Todo
estado vive em diretórios temporários — nenhum teste toca o `server_data` do
workspace.
"""

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile

import server
from core import event_package as ep
from core import paths


# ── Construtores de dados ────────────────────────────────────────────

def _event(event_id, name, client, region="SP"):
    return {
        "id": event_id,
        "name": name,
        "status": "ACTIVE",
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{"id": "SITE-1", "cells": [{"id": "CELL-1", "azimuth": 0, "beamwidth": 120}]}],
        "oss": {"cliente": client, "region": region, "base_url": "", "import_folder": ""},
    }


def _client(client_id, name, regions=(("SP", "https://10.0.0.1:31943"),)):
    return {
        "id": client_id,
        "name": name,
        "logo": "",
        "regionais": [{"region": region, "ip": ip} for region, ip in regions],
    }


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def package(tmp_path) -> bytes:
    """Um `.sepack` real com dois eventos de clientes diferentes."""
    source = tmp_path / "origem"
    for name in ("events", "clientes", "vips", "logos"):
        (source / name).mkdir(parents=True, exist_ok=True)
    _write_json(source / "events" / "barretos-2026.json", _event("barretos-2026", "Barretos 2026", "Vivo"))
    _write_json(source / "events" / "rock-2026.json", _event("rock-2026", "Rock 2026", "TIM", region="RJ"))
    _write_json(source / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    _write_json(
        source / "clientes" / "tim.json",
        _client("tim", "TIM", regions=(("RJ", "https://10.0.0.2:31943"),)),
    )

    preview = ep.preview_package(source, ["barretos-2026", "rock-2026"], name="Teste")
    assert preview.ok, [error.to_dict() for error in preview.errors]
    destination = tmp_path / "pacote.sepack"
    ep.build_package(preview, destination)
    return destination.read_bytes()


@pytest.fixture
def target(tmp_path, monkeypatch) -> Path:
    """Redireciona a importação para um `server_data` temporário e vazio."""
    root = tmp_path / "destino"
    (root / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "data_dir", lambda: root / "data")
    server_data = root / "server_data"
    for name in ("events", "clientes", "vips", "logos"):
        (server_data / name).mkdir(parents=True, exist_ok=True)
    assert paths.server_data_dir() == server_data
    return server_data


def _tamper(package: bytes) -> bytes:
    """Reembala o `.sepack` com um byte trocado dentro do `payload.zip`.

    Corromper o envelope por fora só quebraria o ZIP externo; o que interessa é
    provar que o hash do manifesto pega a adulteração do conteúdo.
    """
    with zipfile.ZipFile(io.BytesIO(package)) as outer:
        envelope = outer.read(ep.ENVELOPE_NAME)
        payload = bytearray(outer.read(ep.PAYLOAD_NAME))
    payload[len(payload) // 2] ^= 0xFF

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as rebuilt:
        rebuilt.writestr(ep.ENVELOPE_NAME, envelope)
        rebuilt.writestr(ep.PAYLOAD_NAME, bytes(payload))
    return buffer.getvalue()


def _upload(data: bytes, filename: str = "pacote.sepack") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _preview(data: bytes, filename: str = "pacote.sepack") -> dict:
    return asyncio.run(server.preview_event_package(_upload(data, filename)))


def _import(data: bytes, expected_sha256: str = "") -> dict:
    return asyncio.run(server.import_event_package(_upload(data), expected_sha256))


def _http_error(call, *args, **kwargs):
    with pytest.raises(server.HTTPException) as excinfo:
        call(*args, **kwargs)
    return excinfo.value


# ── Revisão ──────────────────────────────────────────────────────────

def test_preview_describes_the_plan_without_writing_anything(package, target):
    plan = _preview(package)

    assert plan["ok"] is True
    assert plan["conflict_policy"] == "preserve"
    assert plan["sha256"] and len(plan["sha256"]) == 64
    added = {action["record_id"] for action in plan["actions"] if action["action"] == "add"}
    assert {"barretos-2026", "rock-2026", "vivo", "tim"} <= added

    # A revisão é a etapa que não toca no disco.
    assert list((target / "events").iterdir()) == []
    assert list((target / "clientes").iterdir()) == []


def test_preview_reports_an_invalid_package_instead_of_raising(package, target):
    plan = _preview(_tamper(package))

    assert plan["ok"] is False
    assert "payload.hash_mismatch" in {error["code"] for error in plan["errors"]}
    assert list((target / "events").iterdir()) == []


def test_upload_must_carry_the_package_extension(package, target):
    error = _http_error(_preview, package, "eventos.zip")

    assert error.status_code == 400
    assert list((target / "events").iterdir()) == []


def test_upload_above_the_package_limit_is_refused(target):
    oversized = b"\x00" * (ep.MAX_PACKAGE_BYTES + 1)

    error = _http_error(_preview, oversized)

    assert error.status_code == 413


# ── Importação ───────────────────────────────────────────────────────

def test_import_writes_the_reviewed_package_and_is_idempotent(package, target):
    plan = _preview(package)
    first = _import(package, plan["sha256"])

    assert first["ok"] is True
    assert sorted(path.name for path in (target / "events").iterdir()) == [
        "barretos-2026.json", "rock-2026.json",
    ]
    assert sorted(path.name for path in (target / "clientes").iterdir()) == [
        "tim.json", "vivo.json",
    ]
    assert first["summary"]

    second = _import(package, plan["sha256"])

    assert second["ok"] is True
    assert second["added"] == []
    assert second["summary"] == ["Pacote ja aplicado; nenhum arquivo foi alterado."]


def test_import_refuses_a_file_that_changed_after_the_review(package, target):
    plan = _preview(package)

    error = _http_error(_import, _tamper(package), plan["sha256"])

    assert error.status_code == 409
    assert list((target / "events").iterdir()) == []


def test_central_preserves_a_conflicting_local_event(package, target):
    _import(package)
    local = target / "events" / "barretos-2026.json"
    changed = json.loads(local.read_text(encoding="utf-8"))
    changed["name"] = "Barretos 2026 — ajuste local"
    local.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _import(package)

    assert result["ok"] is True
    assert "events/barretos-2026.json" in result["preserved"]
    assert result["conflicts"]
    # O ajuste do operador sobrevive: a Central nunca substitui.
    assert json.loads(local.read_text(encoding="utf-8"))["name"] == "Barretos 2026 — ajuste local"


def test_central_reconciles_a_known_client_instead_of_duplicating_it(package, target):
    _write_json(target / "clientes" / "vivo.json", {
        "id": "vivo", "name": "Vivo", "logo": "", "regionais": [],
    })

    result = _import(package)

    assert result["ok"] is True
    assert sorted(path.name for path in (target / "clientes").iterdir()) == [
        "tim.json", "vivo.json",
    ]
    assert "clientes/vivo.json" in result["reconciled"]
    regions = json.loads((target / "clientes" / "vivo.json").read_text(encoding="utf-8"))["regionais"]
    assert [item["region"] for item in regions] == ["SP"]


def test_import_never_deletes_events_absent_from_the_package(package, target):
    _write_json(target / "events" / "outro-evento.json", _event("outro-evento", "Outro", "Vivo"))

    _import(package)

    assert (target / "events" / "outro-evento.json").exists()


# ── Recorte da Central ───────────────────────────────────────────────

def test_the_central_accepts_no_path_or_conflict_policy_from_the_browser():
    """Decisão 6: o navegador manda o arquivo, nunca caminho nem política.

    `preserve` é constante no `server.py` — substituir continua exclusivo da
    linha de comando, onde o operador declara a intenção explicitamente.
    """
    import inspect

    source = inspect.getsource(server.import_event_package)
    assert 'conflict_policy="preserve"' in source

    for endpoint in (server.preview_event_package, server.import_event_package):
        names = set(inspect.signature(endpoint).parameters)
        assert names <= {"file", "expected_sha256"}, names
