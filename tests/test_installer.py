import json
from pathlib import Path


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _event(event_id: str, name: str, client: str, *, radio: bool = False) -> dict:
    cell = {"id": "CELL-1", "azimuth": 0, "beamwidth": 120}
    if radio:
        cell.update({"tech": "4G", "frequency": "850"})
    return {
        "id": event_id,
        "name": name,
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{"id": "SITE-1", "cells": [cell]}],
        "oss": {"cliente": client, "region": "SP", "base_url": ""},
    }


def _client(client_id: str, name: str) -> dict:
    return {
        "id": client_id,
        "name": name,
        "logo": "",
        "regionais": [{"region": "SP", "ip": "https://127.0.0.1:31943"}],
    }


def test_prepare_installer_seed_keeps_only_roadshow_tim(tmp_path):
    from tools.prepare_installer_seed import prepare

    source = tmp_path / "source"
    _write(source / "events" / "road.json", _event("road", "Road Show SP", "TIM"))
    _write(source / "events" / "other.json", _event("other", "Festival", "TIM"))
    _write(source / "events" / "vivo.json", _event("vivo", "RoadShow Vivo", "Vivo"))
    _write(source / "clientes" / "tim.json", _client("tim", "TIM"))
    _write(source / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    _write(source / "vips" / "tim.json", {"id": "vip-tim", "cliente": "TIM"})
    _write(source / "vips" / "vivo.json", {"id": "vip-vivo", "cliente": "Vivo"})

    destination = tmp_path / "seed"
    manifest = prepare(source, destination)

    assert manifest["events"] == ["road.json"]
    assert manifest["clientes"] == ["tim.json"]
    assert manifest["vips"] == ["tim.json"]
    assert not (destination / "events" / "other.json").exists()


def test_seed_operator_data_preserves_existing_and_adds_missing(tmp_path, monkeypatch):
    from core import seed

    bundled = tmp_path / "bundle" / "server_data"
    operator = tmp_path / "operator" / "server_data"
    _write(bundled / "events" / "road.json", {
        "id": "road", "name": "RoadShow", "oss": {"cliente": "TIM"}
    })
    _write(operator / "events" / "custom.json", {"id": "custom", "name": "Operador"})
    monkeypatch.setattr(seed, "bundled_seed_dir", lambda: bundled)
    monkeypatch.setattr(seed, "operator_seed_dir", lambda: operator)

    assert seed.seed_operator_data() == operator
    assert (operator / "events" / "custom.json").exists()
    assert (operator / "events" / "road.json").exists()


def test_validate_seed_rejects_non_roadshow_event(tmp_path):
    from core.seed import validate_seed

    _write(tmp_path / "events" / "festival.json", {
        "id": "festival", "name": "Festival", "oss": {"cliente": "TIM"}
    })
    _write(tmp_path / "clientes" / "tim.json", {"id": "tim", "name": "TIM"})
    result = validate_seed(tmp_path)
    assert result["ok"] is False
    assert any("fora do recorte RoadShow" in error for error in result["errors"])


def test_frozen_paths_use_local_app_data(tmp_path, monkeypatch):
    import core.paths as paths

    monkeypatch.delenv("SMARTEVENTS_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.delattr(paths.sys, "_MEIPASS", raising=False)
    assert paths.data_dir() == tmp_path / "SmartEvents"


def test_frozen_paths_use_profile_specific_data_dir(tmp_path, monkeypatch):
    import core.paths as paths

    bundle = tmp_path / "bundle"
    _write(bundle / "build-profile.json", {"runtime_data_dir": "SmartEvents-Vivo-Test"})
    monkeypatch.delenv("SMARTEVENTS_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(bundle), raising=False)

    assert paths.data_dir() == tmp_path / "local" / "SmartEvents-Vivo-Test"


def test_prepare_vivo_profile_keeps_exact_event_and_validates_radio(tmp_path):
    from core.seed import validate_seed
    from tools.prepare_installer_seed import prepare

    source = tmp_path / "source"
    event = _event("barretos", "Barretos", "Vivo", radio=True)
    event["integration"] = {
        "pm_tasks": [
            {"tech": "4G", "task_id": 2460},
            {"tech": "NRCELL", "task_id": 2461},
        ]
    }
    _write(source / "events" / "barretos.json", event)
    _write(source / "events" / "outro.json", _event("outro", "Outro", "Vivo", radio=True))
    _write(source / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    profile = {
        "id": "vivo-test",
        "profile_name": "Vivo Test",
        "client": "Vivo",
        "event_ids": ["barretos"],
        "required_pm_tasks": [
            {"tech": "4G", "task_id": 2460},
            {"tech": "NRCELL", "task_id": 2461},
        ],
        "require_cell_radio_metadata": True,
    }

    manifest = prepare(source, tmp_path / "seed", profile)
    result = validate_seed(tmp_path / "seed")

    assert manifest["events"] == ["barretos.json"]
    assert result["ok"] is True
    assert result["profile"]["client"] == "Vivo"


def test_validate_collection_accepts_multiple_clients_and_exact_event_list(tmp_path):
    from core.seed import validate_collection

    events = [
        (Path("tim.json"), _event("tim-a", "Evento TIM", "TIM")),
        (Path("vivo.json"), _event("vivo-a", "Evento Vivo", "Vivo")),
    ]
    clientes = [
        (Path("tim.json"), _client("tim", "TIM")),
        (Path("vivo.json"), _client("vivo", "Vivo")),
    ]
    vips = [(Path("vip.json"), {"id": "vip-a", "name": "VIP A", "cliente": "Vivo"})]

    assert validate_collection(
        events, clientes, vips, expected_event_ids=["tim-a", "vivo-a"]
    ) == []

    faltando = validate_collection(events, clientes, vips, expected_event_ids=["tim-a"])
    assert any("diferem da lista esperada" in error for error in faltando)

    sem_cliente = validate_collection(events, [clientes[0]], vips)
    assert any("Evento sem cliente cadastrado" in error for error in sem_cliente)
    assert any("VIP sem cliente cadastrado" in error for error in sem_cliente)


def test_installer_seed_never_carries_credentials_or_history(tmp_path):
    from tools.prepare_installer_seed import prepare

    source = tmp_path / "source"
    _write(source / "events" / "road.json", _event("road", "RoadShow SP", "TIM"))
    _write(source / "clientes" / "tim.json", _client("tim", "TIM"))
    # Lixo que jamais pode viajar num artefato distribuido.
    _write(source / "credentials.json", {"TIM": {"_shared": {"user": "x", "password": "y"}}})
    _write(source / "session.json", {"cookie": "abc"})
    (source / "smart_events.db").write_bytes(b"SQLite format 3\x00")
    (source / "logs").mkdir(parents=True, exist_ok=True)
    (source / "logs" / "smart_events.log").write_text("log", encoding="utf-8")

    destination = tmp_path / "seed"
    prepare(source, destination)

    produced = {path.name for path in destination.rglob("*") if path.is_file()}
    assert "credentials.json" not in produced
    assert "session.json" not in produced
    assert not any(name.endswith((".db", ".log")) for name in produced)


def test_validate_profile_seed_rejects_missing_radio_and_pm_task(tmp_path):
    from core.seed import validate_seed

    _write(tmp_path / "events" / "barretos.json", _event("barretos", "Barretos", "Vivo"))
    _write(tmp_path / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    _write(
        tmp_path / "seed-manifest.json",
        {
            "schema_version": 2,
            "profile": {
                "id": "vivo-test",
                "client": "Vivo",
                "event_ids": ["barretos"],
                "required_pm_tasks": [{"tech": "4G", "task_id": 2460}],
                "require_cell_radio_metadata": True,
            },
        },
    )

    result = validate_seed(tmp_path)

    assert result["ok"] is False
    assert any("Tarefas PM obrigatorias ausentes" in error for error in result["errors"])
    assert any("Celulas sem tecnologia/frequencia" in error for error in result["errors"])
