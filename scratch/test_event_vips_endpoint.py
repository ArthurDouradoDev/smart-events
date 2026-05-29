import os
import sys
import json
from pathlib import Path

# Add root folder to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server import get_event_vips, EVENTS_DIR

def test_event_vips_endpoint():
    # Setup mock event with VIPs
    mock_event_path = EVENTS_DIR / "test-mock-event.json"
    mock_event_data = {
        "id": "test-mock-event",
        "name": "Test Mock Event",
        "vips": [
            {"id": "vip-alpha", "task_id": 9991},
            {"id": "vip-beta", "task_id": 9992}
        ]
    }
    
    with open(mock_event_path, "w", encoding="utf-8") as f:
        json.dump(mock_event_data, f, indent=4)
        
    try:
        # Invoke the endpoint logic
        data = get_event_vips()
        
        print(f"Returned data: {data}")
        assert isinstance(data, list), "Expected response to be a list"
        
        # Filter for our mock event associations
        mock_assocs = [item for item in data if item.get("event_id") == "test-mock-event"]
        assert len(mock_assocs) == 2, f"Expected 2 mock associations, found {len(mock_assocs)}"
        
        ids = {item["vip_id"]: item["task_id"] for item in mock_assocs}
        assert "vip-alpha" in ids and ids["vip-alpha"] == 9991, "vip-alpha association is incorrect"
        assert "vip-beta" in ids and ids["vip-beta"] == 9992, "vip-beta association is incorrect"
        
        print("SUCCESS: mock data parsing verified successfully!")
        
    finally:
        # Teardown
        if mock_event_path.exists():
            mock_event_path.unlink()
            print("Teardown: Mock event file removed.")

if __name__ == "__main__":
    test_event_vips_endpoint()
