import json
from pathlib import Path

import pytest


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


# ── Fase 3 — build-base generico e instalacao por pacote ─────────────

def _generic_operator_data(tmp_path, monkeypatch):
    """Aponta semente, pasta do operador e `server_data` para diretorios isolados."""
    from core import base_build, paths, seed as seed_module

    bundled = base_build.prepare_generic_seed(tmp_path / "bundle" / "server_data", "1.0.0")
    operator = tmp_path / "operator"
    monkeypatch.setattr(seed_module, "bundled_seed_dir", lambda: bundled)
    monkeypatch.setattr(seed_module, "operator_seed_dir", lambda: operator / "server_data")
    monkeypatch.setattr(paths, "data_dir", lambda: operator)
    monkeypatch.setattr(paths, "server_data_dir", lambda: operator / "server_data")
    return operator


def test_generic_base_seed_passes_the_program_self_test(tmp_path, monkeypatch):
    """O build-base nao tem evento algum — e isso e um diagnostico APROVADO."""
    from core import self_test

    operator = _generic_operator_data(tmp_path, monkeypatch)

    detail = self_test._seeds()

    assert "generico" in detail
    assert "nenhum evento embutido" in detail
    for name in ("events", "clientes", "vips", "logos"):
        assert (operator / "server_data" / name).is_dir()


def test_self_test_only_demands_events_when_the_distribution_declares_them(tmp_path, monkeypatch):
    from core import self_test

    operator = _generic_operator_data(tmp_path, monkeypatch)

    # Sem expectativa declarada, nada exige evento.
    report = operator / "diagnostics" / "imports" / "installer-import.json"
    with pytest.raises(RuntimeError, match="Relatorio de importacao ausente"):
        self_test._package_report(report)

    _write(report, {
        "ok": True, "package_id": "pkg-1",
        "added": ["events/barretos.json"], "reconciled": [], "preserved": [], "conflicts": [],
    })
    assert "adicionados=1" in self_test._package_report(report)

    # Importacao reprovada nunca pode virar um diagnostico aprovado.
    _write(report, {
        "ok": False,
        "errors": [{"code": "package.invalid", "message": "hash divergente"}],
    })
    with pytest.raises(RuntimeError, match="hash divergente"):
        self_test._package_report(report)


def test_expected_events_must_exist_in_server_data_and_in_the_database(tmp_path, monkeypatch):
    from core import database as db
    from core import self_test

    operator = _generic_operator_data(tmp_path, monkeypatch)
    monkeypatch.setenv("SMARTEVENTS_DATA_DIR", str(operator))

    with pytest.raises(RuntimeError, match="ausentes no server_data"):
        self_test._expected_events(["barretos-2026"])

    event = _event("barretos-2026", "Barretos", "Vivo")
    event.update({"sites": [{"id": "SITE-1", "name": "S1", "lat": -20.0, "lng": -48.0, "cells": []}]})
    _write(operator / "server_data" / "events" / "barretos-2026.json", event)

    detail = self_test._expected_events(["barretos-2026"])

    assert "1 evento(s)" in detail
    assert db.get_event("barretos-2026") is not None


def test_local_sync_is_incremental_and_never_deletes_unrelated_events(tmp_path, monkeypatch):
    """Importar pacote nao pode acionar o caminho autoritativo do `sync_events_*`."""
    from core import database as db
    from core import paths

    operator = tmp_path / "operator"
    monkeypatch.setenv("SMARTEVENTS_DATA_DIR", str(operator))
    monkeypatch.setattr(paths, "server_data_dir", lambda: operator / "server_data")
    db.init_db()

    def _persisted(event_id, name):
        return {
            "id": event_id, "name": name, "status": "SCHEDULED",
            "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
            "sites": [{"id": "SITE-1", "name": "S1", "lat": -20.0, "lng": -48.0, "cells": []}],
            "oss": {"cliente": "Vivo", "region": "SP"},
        }

    # Evento que o operador ja tinha, e que NAO vem no pacote novo.
    db.save_event(_persisted("antigo", "Evento antigo"))
    _write(operator / "server_data" / "events" / "novo.json", _persisted("novo", "Evento novo"))

    stats = db.sync_events_from_local_files(["novo"])

    assert stats == {"sincronizados": 1, "erros": 0, "removidos": 0}
    assert db.get_event("novo") is not None
    # A garantia da fase: nada some por não estar no pacote.
    assert db.get_event("antigo") is not None


def test_local_sync_preserves_an_advanced_local_status(tmp_path, monkeypatch):
    from core import database as db
    from core import paths

    operator = tmp_path / "operator"
    monkeypatch.setenv("SMARTEVENTS_DATA_DIR", str(operator))
    monkeypatch.setattr(paths, "server_data_dir", lambda: operator / "server_data")
    db.init_db()

    event = {
        "id": "ativo", "name": "Evento ativo", "status": "ACTIVE",
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{"id": "SITE-1", "name": "S1", "lat": -20.0, "lng": -48.0, "cells": []}],
        "oss": {"cliente": "Vivo", "region": "SP"},
    }
    db.save_event(event)
    _write(operator / "server_data" / "events" / "ativo.json", {**event, "status": "SCHEDULED"})

    db.sync_events_from_local_files()

    assert db.get_event("ativo")["status"] == "ACTIVE"


def test_uninstall_removes_the_program_and_the_association_but_keeps_operator_data():
    iss = (Path(__file__).parents[1] / "installer" / "SmartEvents.iss").read_text(encoding="utf-8")

    delete = iss[iss.index("[UninstallDelete]"):iss.index("[Code]")]
    # So a pasta do programa e removida; `%LOCALAPPDATA%\SmartEvents` sobrevive a
    # desinstalacao — e por isso que reinstalar reencontra os dados preservados.
    assert 'Name: "{app}"' in delete
    assert "{localappdata}" not in delete
    assert "{userappdata}" not in delete

    registry = iss[iss.index("[Registry]"):iss.index("[Run]")]
    assert "uninsdeletekey" in registry
    assert "{localappdata}" not in registry
