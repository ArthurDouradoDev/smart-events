import sqlite3
import json
from pathlib import Path

db_path = Path("data/smart_events.db")
if not db_path.exists():
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT id, name, status, config_json FROM events").fetchall()
print(f"Total events in smart_events.db: {len(rows)}")
for r in rows:
    config = json.loads(r["config_json"])
    print(f"Event: id={r['id']}, name={r['name']}, status={r['status']}")
    print(f"  Keys in config_json: {list(config.keys())}")
    print(f"  Number of sites in config_json: {len(config.get('sites', []))}")
    print(f"  Number of vips in config_json: {len(config.get('vips', []))}")
conn.close()
