"""Fase 4 — assinatura e verificacao do payload do ``.sepack``.

Usa o ``ISSigTool.exe`` real (cache local em ``.build-tools/InnoSetup7``, o
mesmo usado para compilar o Setup) para gerar pares de chave efemeros por
teste. Nenhuma chave privada real ou de producao e usada ou referenciada
aqui; tudo vive em ``tmp_path`` e e descartado ao final do teste.
"""

import json
import subprocess

import pytest

from core import event_package as ep
from core import package_signing as psig


pytestmark = pytest.mark.skipif(
    not psig.is_issigtool_available(),
    reason="ISSigTool.exe indisponivel neste ambiente (cache .build-tools/InnoSetup7 ausente)",
)


# ── Construtores de dados (mesmo padrao de tests/test_event_package.py) ──

def _event(event_id, name, client, region="SP"):
    return {
        "id": event_id,
        "name": name,
        "status": "ACTIVE",
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


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _source(tmp_path):
    root = tmp_path / "source" / "server_data"
    for name in ("events", "clientes", "vips", "logos"):
        (root / name).mkdir(parents=True, exist_ok=True)
    _write_json(root / "events" / "evento-a.json", _event("evento-a", "Evento A", "Vivo"))
    _write_json(root / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    return root


def _preview(tmp_path):
    preview = ep.preview_package(_source(tmp_path), ["evento-a"], name="Pacote assinado")
    assert preview.ok, [item.message for item in preview.errors]
    return preview


# ── Chaves de teste ───────────────────────────────────────────────────

def _generate_keypair(tmp_path, name="release"):
    private_key = tmp_path / f"{name}.iskey"
    public_key = tmp_path / f"{name}.iskeypub"
    tool = psig.default_issigtool_path()
    subprocess.run(
        [str(tool), f"--key-file={private_key}", "generate-private-key"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [str(tool), f"--key-file={private_key}", "export-public-key", str(public_key)],
        check=True, capture_output=True, text=True,
    )
    fields = psig._parse_issig_text(public_key.read_text())
    return private_key, public_key, fields["key-id"]


def _keys_dir(tmp_path, entries, *, dirname="keys"):
    """``entries``: lista de ``(key_id, public_key_path, active_from, retired_at)``."""
    keys_dir = tmp_path / dirname
    keys_dir.mkdir(exist_ok=True)
    registry = []
    for key_id, public_key_path, active_from, retired_at in entries:
        (keys_dir / f"{key_id}.iskeypub").write_bytes(public_key_path.read_bytes())
        registry.append({
            "key_id": key_id, "label": "test", "active_from": active_from, "retired_at": retired_at,
        })
    (keys_dir / psig.REGISTRY_NAME).write_text(json.dumps(registry), encoding="utf-8")
    return keys_dir


# ── core/package_signing.py: assinatura e verificacao isoladas ───────

def test_signed_payload_is_accepted_with_allowed_public_key(tmp_path):
    private_key, public_key, key_id = _generate_keypair(tmp_path)
    keys_dir = _keys_dir(tmp_path, [(key_id, public_key, "2020-01-01", None)])
    payload = b"payload de teste"

    signature = psig.sign_payload_bytes(payload, private_key)
    result = psig.verify_payload_signature(payload, signature, keys_dir=keys_dir)

    assert result.ok
    assert result.status == psig.STATUS_OK
    assert result.key_id == key_id


def test_tampered_payload_is_rejected_before_extraction(tmp_path):
    private_key, public_key, key_id = _generate_keypair(tmp_path)
    keys_dir = _keys_dir(tmp_path, [(key_id, public_key, "2020-01-01", None)])
    payload = b"payload de teste"
    signature = psig.sign_payload_bytes(payload, private_key)

    result = psig.verify_payload_signature(payload + b"adulterado", signature, keys_dir=keys_dir)

    assert not result.ok
    assert result.status == psig.STATUS_INVALID


def test_signature_from_unknown_key_is_rejected(tmp_path):
    private_key, public_key, key_id = _generate_keypair(tmp_path, "outra-chave")
    # Registro vazio: nenhuma chave e reconhecida, mesmo com assinatura matematicamente valida.
    keys_dir = _keys_dir(tmp_path, [])
    payload = b"payload de teste"
    signature = psig.sign_payload_bytes(payload, private_key)

    result = psig.verify_payload_signature(payload, signature, keys_dir=keys_dir)

    assert not result.ok
    assert result.status == psig.STATUS_UNKNOWN_KEY


def test_key_rotation_accepts_active_and_rejects_retired_key(tmp_path):
    private_key, public_key, key_id = _generate_keypair(tmp_path)
    payload = b"payload de teste"
    signature = psig.sign_payload_bytes(payload, private_key)

    active_keys = _keys_dir(tmp_path, [(key_id, public_key, "2020-01-01", None)], dirname="active")
    assert psig.verify_payload_signature(payload, signature, keys_dir=active_keys).ok

    retired_keys = _keys_dir(
        tmp_path, [(key_id, public_key, "2020-01-01", "2021-01-01")], dirname="retired",
    )
    result = psig.verify_payload_signature(payload, signature, keys_dir=retired_keys)
    assert not result.ok
    assert result.status == psig.STATUS_RETIRED_KEY


def test_private_key_path_and_contents_never_enter_artifacts_or_logs(tmp_path):
    private_key, _public_key, _key_id = _generate_keypair(tmp_path, "segredo-de-release")
    payload = b"payload de teste"

    signature = psig.sign_payload_bytes(payload, private_key)
    fields = psig.parse_signature(signature)

    # A assinatura carrega hash/chave publica, nunca o caminho ou o conteudo
    # da chave privada usada para gera-la.
    assert str(private_key) not in json.dumps(fields)
    assert "segredo-de-release" not in json.dumps(fields)


# ── Integracao com core/event_package.py ─────────────────────────────

def test_build_package_with_signing_key_embeds_verifiable_signature(tmp_path):
    private_key, public_key, key_id = _generate_keypair(tmp_path)
    keys_dir = _keys_dir(tmp_path, [(key_id, public_key, "2020-01-01", None)])

    preview = _preview(tmp_path)
    destination = tmp_path / "pacote.sepack"
    ep.build_package(preview, destination, signing_key_path=private_key)

    inspection = ep.validate_package(destination, keys_dir=keys_dir)
    assert inspection.ok, [item.message for item in inspection.errors]
    assert inspection.envelope["signed"] is True
    assert inspection.envelope["signature_status"] == "ok"
    assert inspection.envelope["signature_key_id"] == key_id


def test_missing_signature_requires_explicit_dev_mode(tmp_path):
    preview = _preview(tmp_path)
    destination = tmp_path / "pacote.sepack"
    ep.build_package(preview, destination)  # sem signing_key_path: sai sem assinatura

    dev = ep.validate_package(destination, signature_policy=ep.SIGNATURE_POLICY_DEVELOPMENT)
    assert dev.ok
    assert any("desenvolvimento" in message for message in dev.warnings)

    prod = ep.validate_package(destination, signature_policy=ep.SIGNATURE_POLICY_PRODUCTION)
    assert not prod.ok
    assert {item.code for item in prod.errors} == {"signature.missing"}


def test_signature_from_unknown_key_is_rejected_by_validate_package(tmp_path):
    private_key, _public_key, _key_id = _generate_keypair(tmp_path)
    empty_keys_dir = tmp_path / "no-keys"
    empty_keys_dir.mkdir()
    (empty_keys_dir / psig.REGISTRY_NAME).write_text("[]", encoding="utf-8")

    preview = _preview(tmp_path)
    destination = tmp_path / "pacote.sepack"
    ep.build_package(preview, destination, signing_key_path=private_key)

    # Mesmo em modo desenvolvimento, uma assinatura PRESENTE e invalida (aqui,
    # de chave desconhecida) e sempre recusada -- nunca vira "so um aviso".
    inspection = ep.validate_package(
        destination, signature_policy=ep.SIGNATURE_POLICY_DEVELOPMENT, keys_dir=empty_keys_dir,
    )
    assert not inspection.ok
    assert {item.code for item in inspection.errors} == {"signature.unknown_key"}


def test_tampered_signed_package_is_rejected_and_writes_nothing(tmp_path):
    private_key, public_key, key_id = _generate_keypair(tmp_path)
    keys_dir = _keys_dir(tmp_path, [(key_id, public_key, "2020-01-01", None)])

    preview = _preview(tmp_path)
    destination = tmp_path / "pacote.sepack"
    ep.build_package(preview, destination, signing_key_path=private_key)

    # Corrompe um byte do payload.zip dentro do .sepack (mesmo ataque do
    # criterio de aceite: "corromper um byte para antes do self-test").
    import zipfile
    import io

    with zipfile.ZipFile(destination) as outer:
        envelope_raw = outer.read(ep.ENVELOPE_NAME)
        payload = bytearray(outer.read(ep.PAYLOAD_NAME))
        signature = outer.read(ep.SIGNATURE_NAME)
    payload[-1] ^= 0xFF
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(ep.ENVELOPE_NAME, envelope_raw)
        archive.writestr(ep.PAYLOAD_NAME, bytes(payload))
        archive.writestr(ep.SIGNATURE_NAME, signature)
    destination.write_bytes(buffer.getvalue())

    target = tmp_path / "target"
    result = ep.import_package(destination, target, keys_dir=keys_dir)

    assert not result.ok
    assert not (target / "server_data").exists()
