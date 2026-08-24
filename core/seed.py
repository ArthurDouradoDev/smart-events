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
    """Copia somente arquivos ausentes, preservando ajustes do operador."""
    source = bundled_seed_dir()
    target = operator_seed_dir()
    if source.resolve() == target.resolve():
        return target
    if not source.is_dir():
        raise FileNotFoundError(f"Semente embutida ausente: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for source_path in source.rglob("*"):
        relative = source_path.relative_to(source)
        target_path = target / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        elif not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
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


def _load_manifest(root: Path) -> dict | None:
    path = root / "seed-manifest.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("seed-manifest.json deve conter um objeto.")
    return value


def _client_regions(client: dict) -> dict[str, str]:
    regions = client.get("regionais") or {}
    if isinstance(regions, dict):
        return {str(key).upper(): str(value).strip() for key, value in regions.items()}
    if isinstance(regions, list):
        result = {}
        for item in regions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("region") or "").strip().upper()
            if name:
                result[name] = str(item.get("ip") or item.get("base_url") or "").strip()
        return result
    return {}


def _task_key(value: dict) -> tuple[str, int | str]:
    tech = str(value.get("tech") or "").strip().upper()
    task_id = value.get("task_id")
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        task_id = str(task_id or "").strip()
    return tech, task_id


def _validate_profile_seed(
    root: Path,
    profile: dict,
    events: list[tuple[Path, dict]],
    clientes: list[tuple[Path, dict]],
    vips: list[tuple[Path, dict]],
) -> list[str]:
    errors: list[str] = []
    expected_client = str(profile.get("client") or "").strip()
    expected_event_ids = {str(value).strip() for value in profile.get("event_ids") or []}
    actual_event_ids = {str(event.get("id") or path.stem).strip() for path, event in events}

    if not expected_client:
        errors.append("Perfil da semente sem cliente.")
    if not expected_event_ids:
        errors.append("Perfil da semente sem eventos.")
    if actual_event_ids != expected_event_ids:
        errors.append(
            "Eventos da semente diferem do perfil: "
            f"esperados={sorted(expected_event_ids)}, encontrados={sorted(actual_event_ids)}."
        )

    matching_clients = []
    if len(clientes) != 1:
        errors.append(f"A semente deve conter exatamente um cliente; encontrados: {len(clientes)}.")
    for file_path, client in clientes:
        identity = str(client.get("name") or client.get("id") or "")
        if identity.casefold() != expected_client.casefold():
            errors.append(f"Cliente fora do perfil {expected_client}: {file_path.name}.")
        else:
            matching_clients.append(client)
    regions = _client_regions(matching_clients[0]) if len(matching_clients) == 1 else {}

    required_tasks = {_task_key(value) for value in profile.get("required_pm_tasks") or []}
    require_radio = bool(profile.get("require_cell_radio_metadata", False))
    for file_path, event in events:
        event_client = str((event.get("oss") or {}).get("cliente") or "")
        if event_client.casefold() != expected_client.casefold():
            errors.append(f"Evento nao pertence a {expected_client}: {file_path.name}.")
        polygon = event.get("polygon") or []
        if not isinstance(polygon, list) or len(polygon) < 3:
            errors.append(f"Evento sem poligono valido: {file_path.name}.")
        sites = event.get("sites") or []
        if not isinstance(sites, list) or not sites:
            errors.append(f"Evento sem sites: {file_path.name}.")
            sites = []
        region = str((event.get("oss") or {}).get("region") or "").strip().upper()
        explicit_url = str((event.get("oss") or {}).get("base_url") or "").strip()
        if not explicit_url and (not region or not regions.get(region)):
            errors.append(
                f"Regional OSS sem URL para {expected_client}: {file_path.name} ({region or 'N/D'})."
            )
        actual_tasks = {
            _task_key(value)
            for value in (event.get("integration") or {}).get("pm_tasks") or []
        }
        missing_tasks = sorted(required_tasks - actual_tasks, key=str)
        if missing_tasks:
            errors.append(f"Tarefas PM obrigatorias ausentes em {file_path.name}: {missing_tasks}.")
        if require_radio:
            missing_cells = []
            for site in sites:
                for cell in site.get("cells") or []:
                    if not str(cell.get("tech") or "").strip() or not str(
                        cell.get("frequency") or ""
                    ).strip():
                        missing_cells.append(str(cell.get("id") or "sem-id"))
            if missing_cells:
                sample = ", ".join(missing_cells[:5])
                suffix = "..." if len(missing_cells) > 5 else ""
                errors.append(
                    f"Celulas sem tecnologia/frequencia em {file_path.name}: "
                    f"{len(missing_cells)} ({sample}{suffix})."
                )

    for file_path, vip in vips:
        if str(vip.get("cliente") or "").casefold() != expected_client.casefold():
            errors.append(f"VIP fora do perfil {expected_client}: {file_path.name}.")
    return errors


def validate_seed(path: Path | None = None) -> dict:
    """Valida o recorte declarado no manifesto ou o legado RoadShow/TIM."""
    root = path or bundled_seed_dir()
    errors: list[str] = []
    try:
        events = _json_files(root / "events")
        clientes = _json_files(root / "clientes")
        vips = _json_files(root / "vips")
        manifest = _load_manifest(root)
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)], "path": str(root)}

    profile = manifest.get("profile") if manifest and manifest.get("schema_version") == 2 else None
    if isinstance(profile, dict):
        errors.extend(_validate_profile_seed(root, profile, events, clientes, vips))
    else:
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
        "profile": profile or {"id": "roadshow-tim", "client": "TIM"},
    }
