import json
from pathlib import Path

raw_path = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents\scratch\rio_kpi_response.json")

with open(raw_path, encoding="utf-8") as f:
    data = json.load(f)

unique_counters = set()

# Drill down to results
task_res = data["data"][0]
results = task_res.get("results", [])

for res in results:
    items = res.get("objRes", []) if "objRes" in res else [res]
    for item in items:
        for counter in item.get("counterRes", []):
            name = counter.get("name")
            if name:
                unique_counters.add(name)

print("Unique counter names in the response:")
for name in sorted(unique_counters):
    print(f"  - {name}")
