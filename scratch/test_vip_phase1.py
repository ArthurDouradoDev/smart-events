"""
Teste rápido da Fase 1: banco + sync + API.
Não toca servidor — apenas valida que as funções de banco e os métodos da Api
fazem o que se espera.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import importlib
from core import database as db

# Garante schema atualizado (cria as tabelas novas se necessário)
db.init_db()

print("=" * 60)
print("1) slugify")
print("=" * 60)
print(f"  'Carlos Menezes' → {db.slugify('Carlos Menezes')}")
print(f"  'João Áéíõç!'    → {db.slugify('João Áéíõç!')}")
print()

print("=" * 60)
print("2) save_vip (insert)")
print("=" * 60)
v1 = db.save_vip({"name": "Carlos Menezes", "role": "CEO", "notes": "VIP do GP"})
print(f"  inserido: {v1}")
v2 = db.save_vip({"name": "Ana Rodrigues", "role": "Diretora"})
print(f"  inserido: {v2}")
print()

print("=" * 60)
print("3) save_vip (update do mesmo id)")
print("=" * 60)
v1b = db.save_vip({"id": v1["id"], "name": "Carlos Menezes", "role": "Presidente", "notes": v1["notes"]})
print(f"  atualizado: {v1b}")
print()

print("=" * 60)
print("4) get_vips (listar todos)")
print("=" * 60)
for v in db.get_vips():
    print(f"  {v['id']:25s}  {v['name']:25s} role={v.get('role')}")
print()

print("=" * 60)
print("5) save_event com VIPs no formato NOVO {id, task_id}")
print("=" * 60)
db.save_event({
    "id": "test-event-new", "name": "Teste Novo", "status": "SCHEDULED",
    "start_time": "2026-05-25", "end_time": "2026-05-26",
    "polygon": [], "sites": [],
    "vips": [
        {"id": v1["id"], "task_id": 1925},
        {"id": v2["id"], "task_id": 1926},
    ],
})
print("  ok")
ev_vips = db.get_event_vips("test-event-new")
for v in ev_vips:
    print(f"  {v['id']:25s}  {v['name']:25s} task_id={v['task_id']}")
print()

print("=" * 60)
print("6) save_event com VIPs no formato LEGADO {name, task_id} → migra")
print("=" * 60)
db.save_event({
    "id": "test-event-legacy", "name": "Teste Legado", "status": "SCHEDULED",
    "start_time": "2026-05-25", "end_time": "2026-05-26",
    "polygon": [], "sites": [],
    "vips": [
        {"name": "Roberto Lima", "task_id": 1927},
        {"name": "Carlos Menezes", "task_id": 1928},  # já existente: não cria duplicata
    ],
})
print("  ok")
ev_vips = db.get_event_vips("test-event-legacy")
for v in ev_vips:
    print(f"  {v['id']:25s}  {v['name']:25s} task_id={v['task_id']}")
print()
print(f"  VIPs globais agora: {[v['name'] for v in db.get_vips()]}")
print()

print("=" * 60)
print("7) assign + unassign VIP a evento existente")
print("=" * 60)
db.assign_vip_to_event("test-event-new", "ana-rodrigues", task_id=1999)
print(f"  após assign 1999 pra Ana no evento new:")
for v in db.get_event_vips("test-event-new"):
    print(f"    {v['name']:25s} task_id={v['task_id']}")
db.unassign_vip_from_event("test-event-new", "ana-rodrigues")
print(f"  após unassign Ana do evento new:")
for v in db.get_event_vips("test-event-new"):
    print(f"    {v['name']:25s} task_id={v['task_id']}")
print()

print("=" * 60)
print("8) delete_vip (cascata em event_vips)")
print("=" * 60)
removed = db.delete_vip("roberto-lima")
print(f"  removido roberto-lima: {removed}")
print(f"  VIPs restantes: {[v['name'] for v in db.get_vips()]}")
print(f"  event_vips de test-event-legacy:")
for v in db.get_event_vips("test-event-legacy"):
    print(f"    {v['name']:25s} task_id={v['task_id']}")
print()

print("=" * 60)
print("9) config_json do evento new (após sync)")
print("=" * 60)
cfg = db.get_event("test-event-new")
print(f"  vips no JSON: {cfg.get('vips')}")
print()

# Limpa
db.get_conn().execute("DELETE FROM events WHERE id LIKE 'test-event-%'")
db.get_conn().execute("DELETE FROM vips WHERE id IN ('carlos-menezes','ana-rodrigues','roberto-lima')")
db.get_conn().commit()
print("Teste concluído.")
