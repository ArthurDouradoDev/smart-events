import json
from pathlib import Path

raw_path = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents\scratch\rio_kpi_response.json")

with open(raw_path, encoding="utf-8") as f:
    data = json.load(f)

# Drill down to results
task_res = data["data"][0]
results = task_res.get("results", [])

print(f"Total results: {len(results)}")
if results:
    # Print the structure of the first result item
    first_res = results[0]
    print("\n--- First Result Item Keys ---")
    print(list(first_res.keys()))
    
    items = first_res.get("objRes", []) if "objRes" in first_res else [first_res]
    print(f"Total items in first result: {len(items)}")
    if items:
        first_item = items[0]
        print("\n--- First objRes Item Keys ---")
        print(list(first_item.keys()))
        print("\n--- First objRes Item Content ---")
        # Print everything except the potentially huge lists
        summary_item = {k: v for k, v in first_item.items() if k not in ("objRes",)}
        print(json.dumps(summary_item, indent=2))
        
        # Let's inspect "counterRes" or other metrics in the first item
        print("\n--- Metrics found in the first item ---")
        for k, v in first_item.items():
            if isinstance(v, list) and k == "counterRes":
                print(f"counterRes has {len(v)} elements.")
                for counter in v[:15]:
                    print(f"  Counter name/id: {counter}")
            elif isinstance(v, dict):
                print(f"Dict key '{k}': {list(v.keys())}")
            elif isinstance(v, list):
                print(f"List key '{k}' has {len(v)} items. Sample: {v[:2]}")
            else:
                print(f"Key '{k}': {v}")
