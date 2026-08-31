"""Contrato do pacote .sepack: geracao, validacao, seguranca e importacao.

Todos os testes trabalham em diretorios temporarios; nenhum toca o ``server_data``
do workspace nem exige credencial real.
"""

import io
import json
import warnings
import zipfile
from pathlib import Path

import pytest

from core import event_package as ep


# ── Construtores de dados ────────────────────────────────────────────

def _event(event_id, name, client, region="SP", **extra):
    event = {
        "id": event_id,
        "name": name,
        "status": "ACTIVE",
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{"id": "SITE-1", "cells": [{"id": "CELL-1", "azimuth": 0, "beamwidth": 120}]}],
        "oss": {"cliente": client, "region": region, "base_url": "", "import_folder": ""},
        "integration": {"pm_tasks": [{"tech": "4G", "task_id": 2279}]},
    }
    event.update(extra)
    return event


def _client(client_id, name, regions=(("SP", "https://10.0.0.1:31943"),), logo=""):
    return {
        "id": client_id,
        "name": name,
        "logo": logo,
        "regionais": [{"region": region, "ip": ip} for region, ip in regions],
    }


def _vip(vip_id, name, client, region="SP", **extra):
    vip = {"id": vip_id, "name": name, "cliente": client, "oss": region, "task_id": 2073}
    vip.update(extra)
    return vip


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _server_data(root, events=(), clientes=(), vips=(), logos=()):
    """Monta uma pasta ``server_data`` completa em disco."""
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


def _source(tmp_path, **kwargs):
    return _server_data(tmp_path / "source" / "server_data", **kwargs)


def _build(tmp_path, source, event_ids, *, name="Pacote", filename="pacote.sepack", **kwargs):
    preview = ep.preview_package(source, event_ids, name=name, **kwargs)
    assert preview.ok, [item.message for item in preview.errors]
    destination = tmp_path / filename
    ep.build_package(preview, destination)
    return destination


def _payload_entries(package):
    with zipfile.ZipFile(package) as outer:
        payload = outer.read(ep.PAYLOAD_NAME)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _zip(entries, *, duplicate=None):
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        # Duplicar uma entrada e exatamente o ataque em teste; o aviso do zipfile
        # aqui e esperado.
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry_name, data in entries.items():
                archive.writestr(entry_name, data)
            if duplicate is not None:
                archive.writestr(duplicate, entries[duplicate])
    return buffer.getvalue()


def _craft(path, payload_entries, *, manifest_overrides=None, duplicate=None):
    """Escreve um .sepack arbitrario, para exercitar o validador."""
    files = {
        entry: ep._sha256(data)
        for entry, data in sorted(payload_entries.items())
    }
    manifest = {
        "schema_version": ep.SCHEMA_VERSION,
        "package_id": "11111111-2222-3333-4444-555555555555",
        "name": "Pacote forjado",
        "created_at_utc": "2026-08-31T12:00:00Z",
        "created_by_app_version": "1.0.0",
        "minimum_app_version": "1.0.0",
        "event_ids": sorted(
            entry.split("/")[1][: -len(".json")]
            for entry in payload_entries
            if entry.startswith("events/")
        ),
        "clients": [],
        "files": files,
        "contains_credentials": False,
    }
    manifest.update(manifest_overrides or {})
    entries = dict(payload_entries)
    entries[ep.MANIFEST_NAME] = ep._json_bytes(manifest)
    payload = _zip(entries, duplicate=duplicate)
    envelope = {
        "schema_version": ep.SCHEMA_VERSION,
        "payload": ep.PAYLOAD_NAME,
        "signature": ep.SIGNATURE_NAME,
        "signature_required": False,
        "payload_sha256": ep._sha256(payload),
    }
    path.write_bytes(_zip({
        ep.ENVELOPE_NAME: ep._json_bytes(envelope),
        ep.PAYLOAD_NAME: payload,
    }))
    return path


def _codes(items):
    return {item.code for item in items}


def _local(target):
    return target / "server_data"


def _backups_of(result, relative):
    """Backups de um caminho relativo, sem depender do separador do sistema."""
    directory, name = relative.split("/")
    return [
        path
        for path in (Path(item) for item in result.backups)
        if path.name == name and path.parent.name == directory
    ]


# ── Geracao ──────────────────────────────────────────────────────────

def test_build_package_contains_selected_events_only(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM"), _event("fora", "Fora", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])

    entries = _payload_entries(package)
    assert "events/alvo.json" in entries
    assert "events/fora.json" not in entries


def test_package_includes_every_referenced_client_and_region(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM", region="OUTRAS")],
        clientes=[_client(
            "tim", "TIM",
            regions=(("SP", "https://10.0.0.1:31943"), ("OUTRAS", "https://10.0.0.2:31943")),
        )],
    )
    package = _build(tmp_path, source, ["alvo"])

    entries = _payload_entries(package)
    client = json.loads(entries["clientes/tim.json"])
    regions = {item["region"]: item["ip"] for item in client["regionais"]}
    assert regions["OUTRAS"] == "https://10.0.0.2:31943"


def test_package_supports_events_from_multiple_clients(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("tim-a", "TIM A", "TIM"), _event("vivo-a", "Vivo A", "Vivo")],
        clientes=[_client("tim", "TIM"), _client("vivo", "Vivo")],
    )
    package = _build(tmp_path, source, ["tim-a", "vivo-a"])

    inspection = ep.validate_package(package)
    assert inspection.ok, [item.message for item in inspection.errors]
    assert set(inspection.manifest.clients) == {"TIM", "Vivo"}
    assert set(inspection.records["cliente"]) == {"tim", "vivo"}


def test_explicit_vip_references_win_over_legacy_fallback(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM", vips=["vip-explicito"])],
        clientes=[_client("tim", "TIM")],
        vips=[_vip("vip-explicito", "Explicito", "TIM"), _vip("vip-legado", "Legado", "TIM")],
    )
    preview = ep.preview_package(source, ["alvo"])

    assert preview.explicit_vip_ids == ["vip-explicito"]
    assert preview.fallback_vip_ids == []
    assert [vip["id"] for vip in preview.vips] == ["vip-explicito"]


def test_legacy_vip_fallback_is_reported_for_review(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM", region="SP")],
        clientes=[_client("tim", "TIM")],
        vips=[_vip("compativel", "Compativel", "TIM", region="SP"),
              _vip("outra-regional", "Outra", "TIM", region="RJ")],
    )
    preview = ep.preview_package(source, ["alvo"])

    assert preview.fallback_vip_ids == ["compativel"]
    assert any("Fallback de compatibilidade" in message for message in preview.warnings)
    # Nunca inclui todos os VIPs do cliente sem considerar a regional.
    assert "outra-regional" not in {vip["id"] for vip in preview.vips}


def test_credentials_sessions_databases_and_logs_are_rejected(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM", oss={
            "cliente": "TIM", "region": "SP", "base_url": "", "password": "segredo",
        })],
        clientes=[_client("tim", "TIM")],
    )
    preview = ep.preview_package(source, ["alvo"])
    assert not preview.ok
    assert "secret.forbidden_field" in _codes(preview.errors)

    for forbidden in ("credentials.json", "events/session.json", "clientes/smart_events.db",
                      "vips/coleta.log"):
        package = _craft(tmp_path / f"{ep._slug(forbidden)}.sepack", {forbidden: b"{}"})
        inspection = ep.validate_package(package)
        assert not inspection.ok, forbidden
        assert _codes(inspection.errors) & {"entry.forbidden", "entry.unsafe_path"}, forbidden


def test_manifest_hashes_cover_every_payload_file(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM", logo="tim.png")],
        vips=[_vip("vip-a", "VIP A", "TIM")],
        logos=[("tim.png", b"imagem-original")],
    )
    package = _build(tmp_path, source, ["alvo"])

    entries = _payload_entries(package)
    manifest = json.loads(entries[ep.MANIFEST_NAME])
    assert set(manifest["files"]) == set(entries) - {ep.MANIFEST_NAME}
    for name, digest in manifest["files"].items():
        assert ep._sha256(entries[name]) == digest

    # Um byte trocado no payload derruba a validacao antes de qualquer uso.
    payload_entries = dict(entries)
    payload_entries["events/alvo.json"] = payload_entries["events/alvo.json"] + b" "
    _craft(tmp_path / "adulterado.sepack", {
        name: data for name, data in payload_entries.items() if name != ep.MANIFEST_NAME
    }, manifest_overrides={"files": manifest["files"]})
    inspection = ep.validate_package(tmp_path / "adulterado.sepack")
    assert "file.hash_mismatch" in _codes(inspection.errors)


def test_package_build_is_reproducible_for_same_inputs(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    from datetime import datetime, timezone

    moment = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    package_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    first = tmp_path / "um.sepack"
    second = tmp_path / "dois.sepack"
    for destination in (first, second):
        preview = ep.preview_package(source, ["alvo"], name="Pacote")
        ep.build_package(preview, destination, package_id=package_id, created_at=moment)

    assert first.read_bytes() == second.read_bytes()


def test_build_refuses_to_overwrite_without_force(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    destination = tmp_path / "pacote.sepack"
    preview = ep.preview_package(source, ["alvo"])
    ep.build_package(preview, destination)
    original = destination.read_bytes()

    with pytest.raises(ep.PackageBuildError) as excinfo:
        ep.build_package(ep.preview_package(source, ["alvo"]), destination)
    assert "output.exists" in _codes(excinfo.value.errors)
    assert destination.read_bytes() == original

    ep.build_package(ep.preview_package(source, ["alvo"]), destination, force=True)


# ── Validacao e seguranca ────────────────────────────────────────────

def test_validator_rejects_path_traversal(tmp_path):
    for entry in ("../fora.json", "events/../../fora.json", "/etc/passwd", "events/..\\fora.json"):
        package = _craft(tmp_path / "traversal.sepack", {entry: b"{}"})
        inspection = ep.validate_package(package)
        assert not inspection.ok, entry
        assert "entry.unsafe_path" in _codes(inspection.errors), entry


def test_validator_rejects_duplicate_zip_entries(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    entries = _payload_entries(package)
    forged = tmp_path / "duplicado.sepack"
    _craft(
        forged,
        {name: data for name, data in entries.items() if name != ep.MANIFEST_NAME},
        duplicate="events/alvo.json",
    )

    inspection = ep.validate_package(forged)
    assert "entry.duplicated" in _codes(inspection.errors)


def test_validator_rejects_zip_bomb_limits(tmp_path):
    oversized = tmp_path / "grande.sepack"
    _craft(oversized, {"events/grande.json": b"\0" * (ep.MAX_FILE_BYTES + 1)})
    assert "entry.too_large" in _codes(ep.validate_package(oversized).errors)

    crowded = tmp_path / "cheio.sepack"
    _craft(crowded, {
        f"events/evento-{index}.json": b"{}" for index in range(ep.MAX_PAYLOAD_ENTRIES + 1)
    })
    assert "payload.too_many_entries" in _codes(ep.validate_package(crowded).errors)


def test_newer_schema_or_minimum_version_is_rejected(tmp_path):
    future_schema = tmp_path / "schema.sepack"
    _craft(future_schema, {"events/alvo.json": b"{}"},
           manifest_overrides={"schema_version": ep.SCHEMA_VERSION + 1})
    assert "schema.unsupported" in _codes(ep.validate_package(future_schema).errors)

    future_app = tmp_path / "versao.sepack"
    _craft(future_app, {"events/alvo.json": b"{}"},
           manifest_overrides={"minimum_app_version": "99.0.0"})
    assert "app.too_old" in _codes(ep.validate_package(future_app).errors)


def test_corrupted_payload_stops_before_writing_anything(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    with zipfile.ZipFile(package) as outer:
        envelope = outer.read(ep.ENVELOPE_NAME)
        payload = bytearray(outer.read(ep.PAYLOAD_NAME))
    payload[len(payload) // 2] ^= 0xFF
    package.write_bytes(_zip({ep.ENVELOPE_NAME: envelope, ep.PAYLOAD_NAME: bytes(payload)}))

    target = tmp_path / "operador"
    result = ep.import_package(package, target)

    assert result.ok is False
    assert "payload.hash_mismatch" in _codes(result.errors)
    assert not _local(target).exists()


# ── Importacao ───────────────────────────────────────────────────────

def test_import_is_idempotent(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    target = tmp_path / "operador"

    first = ep.import_package(package, target)
    assert first.ok and first.added

    snapshot = {
        path: path.read_bytes()
        for path in sorted(_local(target).rglob("*"))
        if path.is_file()
    }
    second = ep.import_package(package, target)

    assert second.ok
    assert second.changed is False
    assert second.added == [] and second.reconciled == [] and second.replaced == []
    assert {path: path.read_bytes() for path in snapshot} == snapshot


def test_import_preserves_conflicting_local_event_by_default(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    target = tmp_path / "operador"
    ep.import_package(package, target)

    local_event = _local(target) / "events" / "alvo.json"
    changed = json.loads(local_event.read_text(encoding="utf-8"))
    changed["name"] = "Alvo ajustado pelo operador"
    _write_json(local_event, changed)

    result = ep.import_package(package, target)

    assert result.ok
    assert "events/alvo.json" in result.preserved
    assert json.loads(local_event.read_text(encoding="utf-8"))["name"] == "Alvo ajustado pelo operador"
    assert any(item.record_id == "alvo" and item.resolution == "preserved"
               for item in result.conflicts)


def test_replace_creates_backup_before_overwrite(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    target = tmp_path / "operador"
    ep.import_package(package, target)

    local_event = _local(target) / "events" / "alvo.json"
    changed = json.loads(local_event.read_text(encoding="utf-8"))
    changed["name"] = "Alvo local"
    _write_json(local_event, changed)
    previous = local_event.read_bytes()

    result = ep.import_package(package, target, conflict_policy="replace")

    assert "events/alvo.json" in result.replaced
    assert json.loads(local_event.read_text(encoding="utf-8"))["name"] == "Alvo"
    backups = _backups_of(result, "events/alvo.json")
    assert backups and backups[0].read_bytes() == previous


def test_import_rolls_back_when_commit_fails(tmp_path, monkeypatch):
    source = _source(
        tmp_path,
        events=[_event("um", "Um", "TIM"), _event("dois", "Dois", "Vivo")],
        clientes=[_client("tim", "TIM"), _client("vivo", "Vivo")],
    )
    package = _build(tmp_path, source, ["um", "dois"])
    target = tmp_path / "operador"

    original = ep._write_atomic
    calls = {"count": 0}

    def failing(path, data):
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("disco cheio")
        original(path, data)

    monkeypatch.setattr(ep, "_write_atomic", failing)
    result = ep.import_package(package, target)

    assert result.ok is False
    assert "import.failed" in _codes(result.errors)
    remaining = sorted(
        path.name for path in _local(target).rglob("*.json") if path.is_file()
    )
    assert remaining == []


def test_import_never_deletes_unrelated_events(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("novo", "Novo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["novo"])
    target = tmp_path / "operador"
    _server_data(_local(target), events=[_event("antigo", "Antigo", "TIM")])
    antigo = _local(target) / "events" / "antigo.json"
    previous = antigo.read_bytes()

    result = ep.import_package(package, target)

    assert result.ok
    assert antigo.read_bytes() == previous
    assert (_local(target) / "events" / "novo.json").is_file()


# ── Conciliacao de cadastros compartilhados (decisao 5.1) ────────────

def test_existing_client_is_reconciled_instead_of_duplicated(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("evento-y", "Evento Y", "TIM", region="OUTRAS")],
        clientes=[_client("tim", "TIM", regions=(
            ("SP", "https://10.0.0.1:31943"), ("OUTRAS", "https://10.0.0.2:31943"),
        ))],
    )
    package = _build(tmp_path, source, ["evento-y"])

    target = tmp_path / "operador"
    _server_data(
        _local(target),
        events=[_event("evento-x", "Evento X", "TIM")],
        clientes=[_client("tim", "TIM")],
    )

    result = ep.import_package(package, target)

    assert result.ok
    assert sorted(path.name for path in (_local(target) / "clientes").glob("*.json")) == ["tim.json"]
    assert "clientes/tim.json" in result.reconciled
    merged = json.loads((_local(target) / "clientes" / "tim.json").read_text(encoding="utf-8"))
    assert {item["region"] for item in merged["regionais"]} == {"SP", "OUTRAS"}


def test_reconcile_adds_missing_fields_and_keeps_local_values(tmp_path):
    local = {"id": "tim", "name": "TIM", "logo": "", "observacao_local": "revisar em campo"}
    incoming = {"id": "tim", "name": "TIM Brasil", "logo": "", "contato": "noc@tim"}

    merged, report = ep.reconcile_record(local, incoming, "cliente")

    assert merged["contato"] == "noc@tim"          # campo faltante entra
    assert merged["observacao_local"] == "revisar em campo"  # campo so local sobrevive
    assert merged["name"] == "TIM"                  # divergencia preserva o local
    assert report.added_fields == ["contato"]
    assert report.preserved_fields == ["name"]
    assert [item.field_name for item in report.conflicts] == ["name"]


def test_reconcile_merges_regions_and_preserves_divergent_ip(tmp_path):
    local = _client("tim", "TIM", regions=(("SP", "https://local:31943"),))
    incoming = _client("tim", "TIM", regions=(
        ("SP", "https://pacote:31943"), ("RJ", "https://rj:31943"),
    ))

    merged, report = ep.reconcile_record(local, incoming, "cliente")

    regions = {item["region"]: item["ip"] for item in merged["regionais"]}
    assert regions["SP"] == "https://local:31943"   # IP local divergente e protegido
    assert regions["RJ"] == "https://rj:31943"      # regional faltante e acrescentada
    assert any(item.field_name == "regionais[SP].ip" and item.resolution == "preserved"
               for item in report.conflicts)


def test_non_empty_logo_replaces_with_backup_and_empty_logo_is_ignored(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM", logo="tim.png")],
        logos=[("tim.png", b"imagem-do-pacote")],
    )
    package = _build(tmp_path, source, ["alvo"])

    # Logo local diferente: a imagem do pacote entra, com backup da anterior.
    com_logo = tmp_path / "com-logo"
    _server_data(
        _local(com_logo),
        clientes=[_client("tim", "TIM", logo="tim.png")],
        logos=[("tim.png", b"imagem-local")],
    )
    result = ep.import_package(package, com_logo)
    assert (_local(com_logo) / "logos" / "tim.png").read_bytes() == b"imagem-do-pacote"
    backups = _backups_of(result, "logos/tim.png")
    assert backups and backups[0].read_bytes() == b"imagem-local"

    # Pacote sem logo: o logo local e mantido.
    sem_logo_source = _server_data(
        tmp_path / "fonte-sem-logo" / "server_data",
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM", logo="")],
    )
    sem_logo_package = _build(
        tmp_path, sem_logo_source, ["alvo"], filename="sem-logo.sepack"
    )
    preservado = tmp_path / "preservado"
    _server_data(
        _local(preservado),
        clientes=[_client("tim", "TIM", logo="tim.png")],
        logos=[("tim.png", b"imagem-local")],
    )
    ep.import_package(sem_logo_package, preservado)
    client = json.loads(
        (_local(preservado) / "clientes" / "tim.json").read_text(encoding="utf-8")
    )
    assert client["logo"] == "tim.png"
    assert (_local(preservado) / "logos" / "tim.png").read_bytes() == b"imagem-local"


def test_same_name_with_different_id_warns_instead_of_merging(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim-sp", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])

    target = tmp_path / "operador"
    _server_data(_local(target), clientes=[_client("tim", "TIM")])

    result = ep.import_package(package, target)

    assert result.ok
    assert sorted(path.name for path in (_local(target) / "clientes").glob("*.json")) == ["tim.json"]
    assert any("mesmo nome" in message for message in result.warnings)
    assert any(item.resolution == "skipped" for item in result.conflicts)


def test_reconcile_without_differences_does_not_rewrite_file(tmp_path, monkeypatch):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    target = tmp_path / "operador"
    ep.import_package(package, target)

    written = []
    original = ep._write_atomic

    def recording(path, data):
        written.append(Path(path).name)
        original(path, data)

    monkeypatch.setattr(ep, "_write_atomic", recording)
    result = ep.import_package(package, target)

    assert result.ok and result.changed is False
    assert "tim.json" not in written and "alvo.json" not in written


# ── Planejamento sem gravacao ────────────────────────────────────────

def test_plan_import_describes_actions_without_touching_disk(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    package = _build(tmp_path, source, ["alvo"])
    target = tmp_path / "operador"

    plan = ep.plan_import(package, target)

    assert plan.ok
    assert {action.path for action in plan.actions if action.action == "add"} == {
        "events/alvo.json", "clientes/tim.json",
    }
    assert not _local(target).exists()
