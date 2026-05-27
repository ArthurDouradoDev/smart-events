import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.api import Api
from core import database as db

def test_api():
    print("Testing API methods...")
    api = Api()
    
    print("\n1. get_app_status:")
    print(api.get_app_status())
    
    print("\n2. get_events:")
    events = api.get_events()
    print(f"Loaded {len(events)} events.")
    
    for ev in events:
        event_id = ev["id"]
        print(f"\nTesting for event: {event_id}")
        
        print(f"  a. get_sites:")
        try:
            sites = api.get_sites(event_id)
            print(f"    Loaded {len(sites)} sites.")
        except Exception as e:
            print(f"    ERROR in get_sites: {e}")
            
        print(f"  b. get_vips:")
        try:
            vips = api.get_vips(event_id)
            print(f"    Loaded {len(vips)} vips.")
        except Exception as e:
            print(f"    ERROR in get_vips: {e}")
            
        print(f"  c. get_alerts:")
        try:
            alerts = api.get_alerts(event_id)
            print(f"    Loaded {len(alerts)} alerts.")
        except Exception as e:
            print(f"    ERROR in get_alerts: {e}")

if __name__ == "__main__":
    test_api()
