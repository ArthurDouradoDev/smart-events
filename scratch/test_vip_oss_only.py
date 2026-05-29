import os
import sys
import json
import sqlite3

# Add root folder to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import database as db
from core.collector import HttpCollector

def setup_test_data():
    conn = db.get_conn()
    
    # 1. Clear any existing test data to start fresh
    conn.execute("DELETE FROM vips WHERE id LIKE 'test-mock-%'")
    conn.execute("DELETE FROM events WHERE id LIKE 'test-event-%'")
    conn.commit()

    # 2. Insert mock global VIPs
    conn.execute(
        "INSERT INTO vips (id, name, task_id, oss) VALUES (?, ?, ?, ?)",
        ("test-mock-sp-1", "VIP Sao Paulo 1", 8881, "SP")
    )
    conn.execute(
        "INSERT INTO vips (id, name, task_id, oss) VALUES (?, ?, ?, ?)",
        ("test-mock-rj-1", "VIP Rio 1", 8882, "RJ")
    )
    conn.commit()

    # 3. Insert mock events in central SQLite
    event_sp = {
        "id": "test-event-sp",
        "name": "Test Event SP",
        "oss": {"region": "SP", "base_url": "http://sp-oss.com"}
    }
    event_rj = {
        "id": "test-event-rj",
        "name": "Test Event RJ",
        "oss": {"region": "RJ", "base_url": "http://rj-oss.com"}
    }

    conn.execute(
        "INSERT INTO events (id, name, status, config_json) VALUES (?, ?, ?, ?)",
        ("test-event-sp", "Test Event SP", "ACTIVE", json.dumps(event_sp))
    )
    conn.execute(
        "INSERT INTO events (id, name, status, config_json) VALUES (?, ?, ?, ?)",
        ("test-event-rj", "Test Event RJ", "ACTIVE", json.dumps(event_rj))
    )
    conn.commit()

    return event_sp, event_rj

def cleanup_test_data():
    conn = db.get_conn()
    conn.execute("DELETE FROM vips WHERE id LIKE 'test-mock-%'")
    conn.execute("DELETE FROM events WHERE id LIKE 'test-event-%'")
    conn.commit()
    print("Database cleanup completed.")

def test_vip_oss_only():
    try:
        # Initialize schema if not present (usually already done)
        db.init_db()
        
        event_sp, event_rj = setup_test_data()
        
        print("\n--- Verifying db.get_event_vips ---")
        vips_sp = db.get_event_vips("test-event-sp")
        print(f"VIPs for SP event: {len(vips_sp)} found.")
        # Assert our mock SP VIP is in the list
        mock_sp_found = [v for v in vips_sp if v["id"] == "test-mock-sp-1"]
        assert len(mock_sp_found) == 1, "Mock SP VIP not found in SP list"
        assert mock_sp_found[0]["task_id"] == 8881
        
        # Assert mock RJ VIP is NOT in the SP list
        mock_rj_in_sp = [v for v in vips_sp if v["id"] == "test-mock-rj-1"]
        assert len(mock_rj_in_sp) == 0, "Mock RJ VIP leaked into SP list"

        vips_rj = db.get_event_vips("test-event-rj")
        print(f"VIPs for RJ event: {len(vips_rj)} found.")
        # Assert our mock RJ VIP is in the list
        mock_rj_found = [v for v in vips_rj if v["id"] == "test-mock-rj-1"]
        assert len(mock_rj_found) == 1, "Mock RJ VIP not found in RJ list"
        assert mock_rj_found[0]["task_id"] == 8882
        
        # Assert mock SP VIP is NOT in the RJ list
        mock_sp_in_rj = [v for v in vips_rj if v["id"] == "test-mock-sp-1"]
        assert len(mock_sp_in_rj) == 0, "Mock SP VIP leaked into RJ list"
        
        print("\n--- Verifying HttpCollector Initialization and _load_vips_by_task ---")
        collector_sp = HttpCollector(event_sp, "http://sp-oss.com")
        print(f"Collector SP vips_by_task in __init__: {collector_sp.vips_by_task}")
        assert 8881 in collector_sp.vips_by_task, "SP VIP task_id not loaded in collector __init__"
        assert collector_sp.vips_by_task[8881] == "VIP Sao Paulo 1"
        assert 8882 not in collector_sp.vips_by_task, "RJ VIP leaked into SP collector"
        
        loaded_sp = collector_sp._load_vips_by_task()
        print(f"Collector SP loaded vips_by_task: {loaded_sp}")
        assert loaded_sp.get(8881) == "VIP Sao Paulo 1", "Incorrect vips_by_task mapping for SP"
        assert 8882 not in loaded_sp, "RJ VIP leaked into SP collector loaded tasks"

        collector_rj = HttpCollector(event_rj, "http://rj-oss.com")
        print(f"Collector RJ vips_by_task in __init__: {collector_rj.vips_by_task}")
        assert 8882 in collector_rj.vips_by_task, "RJ VIP task_id not loaded in collector __init__"
        assert collector_rj.vips_by_task[8882] == "VIP Rio 1"
        assert 8881 not in collector_rj.vips_by_task, "SP VIP leaked into RJ collector"
        
        loaded_rj = collector_rj._load_vips_by_task()
        print(f"Collector RJ loaded vips_by_task: {loaded_rj}")
        assert loaded_rj.get(8882) == "VIP Rio 1", "Incorrect vips_by_task mapping for RJ"
        assert 8881 not in loaded_rj, "SP VIP leaked into RJ collector loaded tasks"
        
        print("\nSUCCESS: All VIP-OSS-only tests completed successfully!")

    finally:
        cleanup_test_data()

if __name__ == "__main__":
    test_vip_oss_only()
