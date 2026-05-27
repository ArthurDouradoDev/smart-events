"""Smoke test: garante que todos os módulos carregam após as mudanças."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import core.database as db
import core.collector as collector
import core.scheduler as scheduler
import api.api as api_mod

print("Imports OK")

# Init schema (cria tabelas vips e event_vips se ainda não existem)
db.init_db()
print("init_db OK")

# Sanity check do método novo na API
a = api_mod.Api()
result = a.list_vips()
print(f"list_vips() retornou {len(result)} VIP(s)")
print("Smoke test passou.")
