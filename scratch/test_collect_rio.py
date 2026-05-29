import json
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents")
sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core import database as db
from core.collector import HttpCollector, _REGIONAL_BASE_URLS, _DEFAULT_BASE_URL

def main():
    # Load the REAL rio event from the database
    event_config = db.get_event("vips-rio-tim-jun-2026")
    if not event_config:
        print("Event 'vips-rio-tim-jun-2026' not found in database!")
        return

    oss = event_config.get("oss", {})
    base_url = oss.get("base_url", "")
    if not base_url:
        region = oss.get("region", "SP").upper()
        base_url = _REGIONAL_BASE_URLS.get(region, _DEFAULT_BASE_URL)
        
    print(f"Event: {event_config['id']} | Resolved base_url: {base_url}")
    
    collector = HttpCollector(event_config, base_url)
    
    # We will mimic collect_kpis but save the raw response JSON
    integration = collector.event.get("integration", {})
    sess_data = collector._load_session_data()
    task_id = integration.get("pm_task_id") or sess_data.get("monitoring", {}).get("task_id") or 374

    print(f"Using PM task_id: {task_id}")
    
    # query with empty objNoExecTimes to discover all cell objNos
    now_ms = int(time.time() * 1000)
    payload = [{
        "taskId": task_id,
        "preExecTime": now_ms,
        "objNoExecTimes": []
    }]

    url = f"{collector.base_url}/rest/oss/access/pm/v1/monitor/task/result?nocache={now_ms}"
    s = collector._get_session("monitoring")
    
    print(f"Posting to {url}...")
    try:
        resp = s.post(url, json=payload, timeout=30, headers={"x-non-renewal-session": "true"})
        print(f"Response status: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        
        # Save raw JSON
        out_path = ROOT_DIR / "scratch" / "rio_kpi_response.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved raw response to {out_path}")
        
        # Parse it using collector's _parse_kpi_response
        kpis = collector._parse_kpi_response(data)
        print(f"Parsed {len(kpis)} KPIs from response.")
        if kpis:
            print("Sample parsed KPIs:")
            for k in kpis[:5]:
                print(f"  {k}")
        else:
            # Let's inspect the first item structure in results
            print("No KPIs could be parsed. Inspecting response structure:")
            if "data" in data and data["data"]:
                task_res = data["data"][0]
                results = task_res.get("results", [])
                if results:
                    first_res = results[0]
                    items_to_process = first_res.get("objRes", []) if "objRes" in first_res else [first_res]
                    if items_to_process:
                        print("Keys in the first item to process:")
                        print(json.dumps(items_to_process[0], indent=2))
                        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
