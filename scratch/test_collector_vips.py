"""
Teste end-to-end do HttpCollector.collect_vips após a refatoração.

Executa a coleta real, mostra o que retornou e qualquer erro.
"""

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Logs visíveis
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Silencia logs ruidosos de bibliotecas
logging.getLogger("urllib3").setLevel(logging.WARNING)

from core.collector import HttpCollector  # noqa: E402

with open(ROOT / "sample_event.json", "r", encoding="utf-8") as f:
    event = json.load(f)

print("=" * 70)
print("CENÁRIO 1: Nenhum VIP com task_id (estado padrão do sample_event)")
print("=" * 70)
collector = HttpCollector(event, event["oss"]["base_url"])
result = collector.collect_vips()
print(f">>> Retornou {len(result)} medições (esperado: 0, deve apenas avisar).\n")

print("=" * 70)
print("CENÁRIO 2: Carlos Menezes com task_id=1925 (task real)")
print("=" * 70)
event2 = json.loads(json.dumps(event))  # deep copy
event2["vips"][0]["task_id"] = 1925
collector2 = HttpCollector(event2, event2["oss"]["base_url"])
result2 = collector2.collect_vips()
print(f"\n>>> Retornou {len(result2)} medições.")
for r in result2[:5]:
    print(r)
