import json
from pathlib import Path


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_prepare_installer_seed_keeps_only_roadshow_tim(tmp_path):
    from tools.prepare_installer_seed import prepare

    source = tmp_path / "source"
    _write(source / "events" / "road.json", {
        "id": "road", "name": "Road Show SP", "oss": {"cliente": "TIM"}
    })
    _write(source / "events" / "other.json", {
        "id": "other", "name": "Festival", "oss": {"cliente": "TIM"}
    })
    _write(source / "events" / "vivo.json", {
        "id": "vivo", "name": "RoadShow Vivo", "oss": {"cliente": "Vivo"}
    })
    _write(source / "clientes" / "tim.json", {"id": "tim", "name": "TIM", "logo": ""})
    _write(source / "clientes" / "vivo.json", {"id": "vivo", "name": "Vivo", "logo": ""})
    _write(source / "vips" / "tim.json", {"id": "vip-tim", "cliente": "TIM"})
    _write(source / "vips" / "vivo.json", {"id": "vip-vivo", "cliente": "Vivo"})

    destination = tmp_path / "seed"
    manifest = prepare(source, destination)

    assert manifest["events"] == ["road.json"]
    assert manifest["clientes"] == ["tim.json"]
    assert manifest["vips"] == ["tim.json"]
    assert not (destination / "events" / "other.json").exists()


def test_seed_operator_data_never_overwrites_existing_data(tmp_path, monkeypatch):
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
    assert not (operator / "events" / "road.json").exists()


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
    assert paths.data_dir() == tmp_path / "SmartEvents"
