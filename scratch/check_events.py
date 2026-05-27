import sys
sys.path.insert(0, r"c:\Users\a50057663\Desktop\Automações\SmartEvents")
from core import database as db

# Show all events
events = db.get_events()
print(f"Events in DB: {len(events)}")
for e in events:
    print(f"  - id={e.get('id')}, name={e.get('name')}, sites={len(e.get('sites', []))}")
    for s in e.get('sites', [])[:2]:
        cells = s.get('cells', [])
        cell_ids = [c.get('id') if isinstance(c, dict) else c for c in cells[:5]]
        print(f"    Site {s['id']}: cells={cell_ids}")
