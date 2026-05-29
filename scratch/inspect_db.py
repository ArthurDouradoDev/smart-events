import sqlite3
import os
import json
from pathlib import Path

data_dir = Path("c:/Users/a50057663/Desktop/Automações/SmartEvents/data")

def inspect_db(db_path):
    print(f"\n=== Inspecting DB: {db_path.name} ===")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # Check tables
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"Tables: {tables}")
        
        # Check events
        if "events" in tables:
            events = conn.execute("SELECT id, name, status, start_time, end_time FROM events").fetchall()
            print("Events:")
            for e in events:
                print(f"  - ID: {e['id']}, Name: {e['name']}, Status: {e['status']}, Start: {e['start_time']}, End: {e['end_time']}")
        
        # Check KPI measurements count
        if "kpi_measurements" in tables:
            count = conn.execute("SELECT count(*) FROM kpi_measurements").fetchone()[0]
            print(f"KPI measurements count: {count}")
            if count > 0:
                print("Latest 5 KPI measurements:")
                kpis = conn.execute("SELECT * FROM kpi_measurements ORDER BY timestamp DESC LIMIT 5").fetchall()
                for k in kpis:
                    print(f"  - Site: {k['site_id']}, Cell: {k['cell_id']}, Timestamp: {k['timestamp']}, Metric: {k['metric']}, Value: {k['value']}")
                
                # Check metrics breakdown
                metrics = conn.execute("SELECT metric, count(*) FROM kpi_measurements GROUP BY metric").fetchall()
                print("Metrics breakdown:")
                for m in metrics:
                    print(f"  - {m[0]}: {m[1]} rows")
        else:
            print("No kpi_measurements table in this DB")
            
        conn.close()
    except Exception as e:
        print(f"Error inspecting DB {db_path}: {e}")

# Inspect all db files in data directory
for f in data_dir.glob("*.db"):
    inspect_db(f)
