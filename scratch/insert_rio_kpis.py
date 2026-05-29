import json
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents")
sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from core import database as db
from core.collector import HttpCollector, build_collector

def main():
    # Load event
    event_config = db.get_event("vips-rio-tim-jun-2026")
    if not event_config:
        print("Event 'vips-rio-tim-jun-2026' not found in database!")
        return

    # Build collector
    print("Building collector...")
    collector = build_collector(event_config, mock=False)
    
    print("Collecting KPIs...")
    kpis = collector.collect_kpis()
    print(f"Collected {len(kpis)} KPI measurements.")
    
    if kpis:
        # Check if they have the scaled metrics
        metrics = {}
        for k in kpis:
            m = k["metric"]
            metrics[m] = metrics.get(m, 0) + 1
        print("Metrics breakdown in collected KPIs:")
        for m, count in metrics.items():
            print(f"  - {m}: {count} measurements")
            
        print("Inserting KPI batch into database...")
        db.insert_kpi_batch(kpis)
        print("Insertion complete!")
        
        # Verify from database
        conn = db.get_event_conn("vips-rio-tim-jun-2026")
        row = conn.execute("SELECT count(*) FROM kpi_measurements").fetchone()
        print(f"Total rows in kpi_measurements table now: {row[0]}")
        
        db_metrics = conn.execute("SELECT metric, count(*) FROM kpi_measurements GROUP BY metric").fetchall()
        print("Database metrics breakdown:")
        for dm in db_metrics:
            print(f"  - {dm[0]}: {dm[1]} rows")
    else:
        print("No KPIs collected.")

if __name__ == "__main__":
    main()
