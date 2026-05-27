import sys
import json
sys.path.insert(0, r"c:\Users\a50057663\Desktop\Automações\SmartEvents")
from core import database as db

e = db.get_event("sao-paulo")
sites = e.get("sites", [])

# Search for sites/cells containing GRVIJ2
print("Searching for GRVIJ2 in event cells...")
found = False
for site in sites:
    site_name = site.get("name", "")
    if "GRVIJ" in site_name or "GRVIJ" in site.get("id", ""):
        print(f"Site match: {site['id']} - {site_name}")
        found = True
    for c in site.get("cells", []):
        cid = c.get("id") if isinstance(c, dict) else c
        if "GRVIJ" in str(cid):
            print(f"Cell match: {site['id']} -> {cid}")
            found = True

if not found:
    print("No GRVIJ2 cells found in event")
    
# Show unique cell ID patterns
patterns = set()
for site in sites[:50]:
    for c in site.get("cells", [])[:2]:
        cid = c.get("id") if isinstance(c, dict) else c
        if cid:
            prefix = cid.split("-")[0] if "-" in cid else cid[:3]
            patterns.add(prefix)
print(f"\nCell ID prefixes (from first 50 sites): {sorted(patterns)}")
