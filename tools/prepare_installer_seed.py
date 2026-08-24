"""Gera uma semente minima e auditavel para um perfil de instalacao."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from core.seed import validate_seed


_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON invalido: {path}")
    return value


def legacy_profile() -> dict:
    """Perfil compativel com o comportamento historico do instalador."""

    return {
        "schema_version": 1,
        "id": "roadshow-tim",
        "profile_name": "RoadShow TIM",
        "client": "TIM",
        "event_name_contains": "RoadShow",
        "require_cell_radio_metadata": False,
    }


def load_profile(path: Path) -> dict:
    profile = _load(path)
    required = {
        "id",
        "profile_name",
        "client",
        "event_ids",
        "artifact_basename",
        "setup_basename",
        "runtime_data_dir",
        "install_dir_name",
        "app_id",
        "operator_readme",
    }
    missing = sorted(required - profile.keys())
    if missing:
        raise ValueError("Perfil de build incompleto: " + ", ".join(missing))
    if not _SAFE_IDENTIFIER.fullmatch(str(profile["id"])):
        raise ValueError(f"ID de perfil inseguro: {profile['id']!r}")
    if not isinstance(profile.get("event_ids"), list) or not profile["event_ids"]:
        raise ValueError("Perfil de build deve declarar event_ids.")
    if not re.fullmatch(r"[A-Fa-f0-9-]{36}", str(profile["app_id"])):
        raise ValueError(f"AppId invalido: {profile['app_id']!r}")
    return profile


def _manifest_profile(profile: dict, selected_event_ids: list[str]) -> dict:
    return {
        "id": str(profile.get("id") or "custom"),
        "name": str(profile.get("profile_name") or profile.get("id") or "custom"),
        "client": str(profile.get("client") or ""),
        "event_ids": selected_event_ids,
        "required_pm_tasks": list(profile.get("required_pm_tasks") or []),
        "require_cell_radio_metadata": bool(
            profile.get("require_cell_radio_metadata", False)
        ),
        "runtime_data_dir": str(profile.get("runtime_data_dir") or "SmartEvents"),
    }


def prepare(source: Path, destination: Path, profile: dict | None = None) -> dict:
    profile = dict(profile or legacy_profile())
    expected_client = str(profile.get("client") or "").strip()
    if not expected_client:
        raise ValueError("Perfil sem cliente.")

    if destination.exists():
        shutil.rmtree(destination)
    for name in ("events", "clientes", "vips", "logos"):
        (destination / name).mkdir(parents=True, exist_ok=True)

    requested_event_ids = {
        str(value).strip()
        for value in (profile.get("event_ids") or [])
        if str(value).strip()
    }
    name_filter = "".join(
        str(profile.get("event_name_contains") or "").casefold().split()
    )
    selected_events: list[str] = []
    selected_event_ids: list[str] = []
    for path in sorted((source / "events").glob("*.json")):
        event = _load(path)
        event_id = str(event.get("id") or path.stem).strip()
        normalized_name = "".join(str(event.get("name", "")).casefold().split())
        client = str((event.get("oss") or {}).get("cliente", "")).casefold()
        selected = (
            event_id in requested_event_ids
            if requested_event_ids
            else bool(name_filter and name_filter in normalized_name)
        )
        if selected and client == expected_client.casefold():
            shutil.copy2(path, destination / "events" / path.name)
            selected_events.append(path.name)
            selected_event_ids.append(event_id)

    selected_clients: list[str] = []
    selected_logos: list[str] = []
    for path in sorted((source / "clientes").glob("*.json")):
        client = _load(path)
        identity = str(client.get("name") or client.get("id") or "").casefold()
        if identity != expected_client.casefold():
            continue
        shutil.copy2(path, destination / "clientes" / path.name)
        selected_clients.append(path.name)
        logo_name = str(client.get("logo") or "").strip()
        if logo_name:
            logo_source = source / "logos" / logo_name
            if not logo_source.is_file():
                raise FileNotFoundError(f"Logo do cliente nao encontrado: {logo_source}")
            shutil.copy2(logo_source, destination / "logos" / logo_name)
            selected_logos.append(logo_name)

    selected_vips: list[str] = []
    for path in sorted((source / "vips").glob("*.json")):
        vip = _load(path)
        if str(vip.get("cliente") or "").casefold() != expected_client.casefold():
            continue
        shutil.copy2(path, destination / "vips" / path.name)
        selected_vips.append(path.name)

    manifest = {
        "schema_version": 2,
        "profile": _manifest_profile(profile, selected_event_ids),
        "filter": {
            "client": expected_client,
            "event_ids": sorted(requested_event_ids),
            "event_name_contains": str(profile.get("event_name_contains") or ""),
        },
        "events": selected_events,
        "clientes": selected_clients,
        "vips": selected_vips,
        "logos": sorted(set(selected_logos)),
    }
    (destination / "seed-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = validate_seed(destination)
    if not result.get("ok"):
        raise RuntimeError("Semente invalida: " + "; ".join(result.get("errors", [])))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("server_data"))
    parser.add_argument(
        "--output", type=Path, default=Path("build") / "installer_seed"
    )
    parser.add_argument("--profile-file", type=Path)
    args = parser.parse_args()

    profile = load_profile(args.profile_file) if args.profile_file else None
    manifest = prepare(args.source.resolve(), args.output.resolve(), profile)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
