import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents")
sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core import database as db
from core.collector import HttpCollector

def main():
    # Load the REAL sao-paulo event from the database
    event_config = db.get_event("teste-ribeirao-pires-dt")
    if not event_config:
        print("Event 'sao-paulo' not found in database!")
        return

    sites = event_config.get("sites", [])
    print(f"Event: {event_config['id']} | Sites: {len(sites)} | Integration: {event_config.get('integration', {})}")
    
    # Show the SR-GRVIJ2 site specifically
    for s in sites:
        if s.get("id") == "SR-GRVIJ2":
            print(f"Found SR-GRVIJ2 site: {s}")
            break

    base_url = event_config.get("oss", {}).get("base_url") or "https://10.220.50.9:31943"
    print(f"\nInitializing HttpCollector with base_url={base_url}")
    collector = HttpCollector(event_config, base_url)
    
    print(f"Pre-mapped obj_to_cell: {collector._obj_to_cell}")
    print(f"Cell-to-site mapping (SR-GRVIJ2 cells): {[(k,v) for k,v in collector._cell_to_site.items() if 'GRVIJ' in k]}")

    # Collect KPIs
    print("\nCollecting KPIs from sao-paulo event (real API)...")
    try:
        kpis = collector.collect_kpis()
        print(f"Collected {len(kpis)} KPI measurements.")
        if kpis:
            print("Sample KPIs:")
            for kpi in kpis[:5]:
                print(json.dumps(kpi, indent=2))
        else:
            print("No KPIs collected - checking why...")
            print(f"Post-parse obj_to_cell: {collector._obj_to_cell}")
    except Exception as e:
        print(f"Error collecting KPIs: {e}")
        import traceback
        traceback.print_exc()

    # Collect VIPs
    print("\nCollecting VIPs (trace)...")
    try:
        vips = collector.collect_vips()
        print(f"Collected {len(vips)} VIP measurements.")
        if vips:
            print("Sample VIPs:")
            for v in vips[:3]:
                print(json.dumps(v, indent=2))
    except Exception as e:
        print(f"Error collecting VIPs: {e}")

if __name__ == "__main__":
    main()
