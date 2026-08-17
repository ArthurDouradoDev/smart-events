"""Instalacao e validacao das sementes offline do SmartEvents."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.paths import data_dir, resource_dir


def bundled_seed_dir() -> Path:
    return resource_dir() / "server_data"


def operator_seed_dir() -> Path:
    return data_dir() / "server_data"


def seed_operator_data() -> Path:
    """Copia a semente somente quando ainda nao existe dado do operador."""
    source = bundled_seed_dir()
    target = operator_seed_dir()
    if source.resolve() == target.resolve():
        return target
    if target.exists() and any(target.iterdir()):
        return target
    if not source.is_dir():
        raise FileNotFoundError(f"Semente embutida ausente: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def _json_files(path: Path) -> list[tuple[Path, dict]]:
    result: list[tuple[Path, dict]] = []
    if not path.is_dir():
        return result
    for file_path in sorted(path.glob("*.json")):
        value = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"JSON deve conter um objeto: {file_path.name}")
        result.append((file_path, value))
    return result


def validate_seed(path: Path | None = None) -> dict:
    """Valida que o pacote contem exclusivamente o recorte RoadShow/TIM."""
    root = path or bundled_seed_dir()
    errors: list[str] = []
    try:
        events = _json_files(root / "events")
        clientes = _json_files(root / "clientes")
        vips = _json_files(root / "vips")
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)], "path": str(root)}

    if not events:
        errors.append("Nenhum evento RoadShow foi encontrado na semente.")
    for file_path, event in events:
        name = str(event.get("name", ""))
        cliente = str((event.get("oss") or {}).get("cliente", ""))
        normalized_name = "".join(name.casefold().split())
        if "roadshow" not in normalized_name:
            errors.append(f"Evento fora do recorte RoadShow: {file_path.name} ({name!r}).")
        if cliente.casefold() != "tim":
            errors.append(f"Evento nao pertence a TIM: {file_path.name}.")

    if len(clientes) != 1:
        errors.append(f"A semente deve conter exatamente um cliente; encontrados: {len(clientes)}.")
    for file_path, cliente in clientes:
        identity = str(cliente.get("name") or cliente.get("id") or "")
        if identity.casefold() != "tim":
            errors.append(f"Cliente fora do recorte TIM: {file_path.name}.")

    for file_path, vip in vips:
        if str(vip.get("cliente", "")).casefold() != "tim":
            errors.append(f"VIP fora do recorte TIM: {file_path.name}.")

    return {
        "ok": not errors,
        "errors": errors,
        "path": str(root),
        "events": len(events),
        "clientes": len(clientes),
        "vips": len(vips),
    }
