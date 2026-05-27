import sqlite3
import os

db_path = r"c:\Users\a50057663\Desktop\Automações\SmartEvents\data\smart_events.db"
if not os.path.exists(db_path):
    print("DB does not exist")
    exit()

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get latest KPI entries
rows = conn.execute("SELECT DISTINCT metric FROM kpi_measurements").fetchall()
print("Metrics found in DB:")
for r in rows:
    print(f"  - {r['metric']}")

print("\nSample records for utilization_dl:")
sample = conn.execute("SELECT * FROM kpi_measurements WHERE metric = 'utilization_dl' LIMIT 3").fetchall()
for s in sample:
    print(f"  - site_id={s['site_id']}, cell_id={s['cell_id']}, timestamp={s['timestamp']}, value={s['value']}")

print("\nSample new records ending in Z:")
sample_z = conn.execute("SELECT * FROM kpi_measurements WHERE timestamp LIKE '%Z' LIMIT 5").fetchall()
for s in sample_z:
    print(f"  - metric={s['metric']}, cell_id={s['cell_id']}, timestamp={s['timestamp']}, value={s['value']}")
