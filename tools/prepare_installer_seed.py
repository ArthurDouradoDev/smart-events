"""Gera a semente minima RoadShow/TIM sem modificar os dados de origem."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from core.seed import validate_seed


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON invalido: {path}")
    return value


def prepare(source: Path, destination: Path) -> dict:
    if destination.exists():
        shutil.rmtree(destination)
    for name in ("events", "clientes", "vips", "logos"):
        (destination / name).mkdir(parents=True, exist_ok=True)

    selected_events = []
    for path in sorted((source / "events").glob("*.json")):
        event = _load(path)
        normalized_name = "".join(str(event.get("name", "")).casefold().split())
        cliente = str((event.get("oss") or {}).get("cliente", "")).casefold()
        if "roadshow" in normalized_name and cliente == "tim":
            shutil.copy2(path, destination / "events" / path.name)
            selected_events.append(path.name)

    selected_clients = []
    selected_logos = []
    for path in sorted((source / "clientes").glob("*.json")):
        cliente = _load(path)
        identity = str(cliente.get("name") or cliente.get("id") or "").casefold()
        if identity != "tim":
            continue
        shutil.copy2(path, destination / "clientes" / path.name)
        selected_clients.append(path.name)
        logo = str(cliente.get("logo") or "").strip()
        logo_path = source / "logos" / logo
        if logo and logo_path.is_file():
            shutil.copy2(logo_path, destination / "logos" / logo_path.name)
            selected_logos.append(logo_path.name)

    selected_vips = []
    for path in sorted((source / "vips").glob("*.json")):
        vip = _load(path)
        if str(vip.get("cliente", "")).casefold() == "tim":
            shutil.copy2(path, destination / "vips" / path.name)
            selected_vips.append(path.name)

    result = validate_seed(destination)
    if not result["ok"]:
        raise RuntimeError("Semente rejeitada: " + "; ".join(result["errors"]))
    manifest = {
        "schema_version": 1,
        "filter": "event.name contem RoadShow (ignorando espacos/caixa) e oss.cliente=TIM",
        "events": selected_events,
        "clientes": selected_clients,
        "vips": selected_vips,
        "logos": selected_logos,
    }
    (destination / "seed-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("server_data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare(args.source.resolve(), args.output.resolve())
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
