import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import database as db

# Ensure settings has the local server url
settings = db.get_settings()
print("Settings:", settings)
if not settings.get("server_url"):
    db.save_settings({"server_url": "http://localhost:8000"})
    print("Set server_url to http://localhost:8000")

# Run VIP sync
print("Running sync...")
stats = db.sync_vips_from_server()
print("Sync stats:", stats)

# List all VIPs
print("=" * 60)
print("VIPs in database:")
print("=" * 60)
for v in db.get_vips():
    print(v)

# List VIPs for 'sao-paulo' event
print("=" * 60)
print("Event VIPs for 'sao-paulo':")
print("=" * 60)
for v in db.get_event_vips("sao-paulo"):
    print(v)
