"""
migrate_vips.py — Migração one-shot do cadastro de VIPs.

Percorre server_data/events/*.json e normaliza o campo `vips` de formatos
legados ({name, task_id} ou {name, imsi}) para o novo formato ({id, task_id}).
Cria os arquivos de cadastro global em server_data/vips/{id}.json.

Idempotente: VIPs/eventos já no novo formato são ignorados.
"""
import sys
import json
import re
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
EVENTS_DIR = BASE_DIR / "server_data" / "events"
VIPS_DIR = BASE_DIR / "server_data" / "vips"


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "vip"


def resolve_unique_slug(name: str, existing_ids: set) -> str:
    """Gera slug único: se 'lucas' já existe, tenta 'lucas-2', 'lucas-3', ..."""
    base = slugify(name)
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        # Se o ID já está mapeado para o mesmo nome, reusa.
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def load_vips_catalog() -> dict:
    """Carrega o catálogo existente em server_data/vips/. Retorna {id: vip_dict}."""
    catalog = {}
    for f in VIPS_DIR.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as fp:
                v = json.load(fp)
            if isinstance(v, dict) and "id" in v and "name" in v:
                catalog[v["id"]] = v
        except Exception as e:
            print(f"  [AVISO] Não foi possível ler {f.name}: {e}")
    return catalog


def save_vip_file(vip: dict):
    path = VIPS_DIR / f"{vip['id']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(vip, f, indent=4, ensure_ascii=False)


def migrate():
    VIPS_DIR.mkdir(parents=True, exist_ok=True)

    event_files = sorted(EVENTS_DIR.glob("*.json"))
    if not event_files:
        print("Nenhum arquivo de evento encontrado em server_data/events/.")
        return

    # Carrega catálogo atual de VIPs globais (pode estar vazio)
    vip_catalog: dict[str, dict] = load_vips_catalog()
    # Mapa auxiliar name→id para reusar IDs já criados nesta migração
    name_to_id: dict[str, str] = {v["name"]: v["id"] for v in vip_catalog.values()}

    total_events = 0
    total_vips_created = 0
    total_events_updated = 0

    for event_path in event_files:
        try:
            with open(event_path, encoding="utf-8") as f:
                event = json.load(f)
        except Exception as e:
            print(f"[ERRO] Não foi possível ler {event_path.name}: {e}")
            continue

        raw_vips = event.get("vips") or []
        if not raw_vips:
            print(f"[SKIP] {event_path.name} — sem VIPs.")
            continue

        total_events += 1
        new_vips = []
        event_changed = False

        for v in raw_vips:
            # Já no novo formato?
            if isinstance(v, dict) and v.get("id"):
                new_vips.append({"id": v["id"], "task_id": v.get("task_id")})
                continue

            # Formato legado — precisa de name
            name = (v.get("name") or "").strip()
            if not name:
                print(f"  [AVISO] VIP sem nome em {event_path.name}: {v} — ignorado.")
                continue

            # task_id pode estar em 'task_id' ou 'imsi'
            task_id_raw = v.get("task_id") or v.get("imsi")
            try:
                task_id = int(task_id_raw) if task_id_raw is not None else None
            except (ValueError, TypeError):
                task_id = None

            # Determina o ID global
            if name in name_to_id:
                vip_id = name_to_id[name]
            else:
                vip_id = resolve_unique_slug(name, set(vip_catalog.keys()))
                vip_catalog[vip_id] = {
                    "id": vip_id,
                    "name": name,
                    "role": v.get("role"),
                    "notes": v.get("notes"),
                }
                name_to_id[name] = vip_id
                save_vip_file(vip_catalog[vip_id])
                total_vips_created += 1
                print(f"  [VIP] Criado: {vip_id!r} -> {name!r}")

            new_vips.append({"id": vip_id, "task_id": task_id})
            event_changed = True

        if event_changed:
            event["vips"] = new_vips
            with open(event_path, "w", encoding="utf-8") as f:
                json.dump(event, f, indent=4, ensure_ascii=False)
            total_events_updated += 1
            print(f"  [EVENTO] {event_path.name} atualizado com {len(new_vips)} VIP(s).")

    print()
    print("-" * 50)
    print(f"Migração concluída.")
    print(f"  Eventos processados : {total_events}")
    print(f"  Eventos atualizados : {total_events_updated}")
    print(f"  VIPs criados        : {total_vips_created}")
    print(f"  Catálogo total      : {len(vip_catalog)} VIP(s) em server_data/vips/")


if __name__ == "__main__":
    migrate()
