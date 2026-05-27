import sys
import os
import shutil
import sqlite3
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import database as db

def verify_isolation():
    print("Initializing main database...")
    db.init_db()

    event_a_id = "test-event-a"
    event_b_id = "test-event-b"

    event_a_config = {
        "id": event_a_id,
        "name": "Test Event A",
        "status": "SCHEDULED",
        "sites": [
            {
                "id": "SITE-A1",
                "name": "Site A1",
                "lat": -23.5,
                "lng": -46.5,
                "is_event_site": True,
                "cells": ["CELL-A1-1"]
            }
        ],
        "vips": [
            {"id": "vip-carlos", "name": "Carlos", "task_id": 100}
        ]
    }

    event_b_config = {
        "id": event_b_id,
        "name": "Test Event B",
        "status": "SCHEDULED",
        "sites": [
            {
                "id": "SITE-B1",
                "name": "Site B1",
                "lat": -23.6,
                "lng": -46.6,
                "is_event_site": True,
                "cells": ["CELL-B1-1"]
            }
        ],
        "vips": [
            {"id": "vip-ana", "name": "Ana", "task_id": 200}
        ]
    }

    print("\nSaving Event A and Event B...")
    db.save_event(event_a_config)
    db.save_event(event_b_config)

    # 1. Verify files exist
    db_a_path = db.get_event_db_path(event_a_id)
    db_b_path = db.get_event_db_path(event_b_id)

    print(f"Checking event database files:")
    print(f"  Event A DB Path: {db_a_path} (Exists: {db_a_path.exists()})")
    print(f"  Event B DB Path: {db_b_path} (Exists: {db_b_path.exists()})")

    assert db_a_path.exists(), "Event A database file should exist!"
    assert db_b_path.exists(), "Event B database file should exist!"

    # 2. Verify sites are in event databases, not in the main database
    main_conn = db.get_conn()
    sites_in_main = main_conn.execute("SELECT count(*) as cnt FROM sqlite_master WHERE type='table' AND name='sites'").fetchone()["cnt"]
    if sites_in_main > 0:
        # Sites table might exist in main DB for backward compatibility, but it should be empty for new inserts
        cnt_in_main = main_conn.execute("SELECT count(*) as cnt FROM sites WHERE event_id IN (?, ?)", (event_a_id, event_b_id)).fetchone()["cnt"]
        print(f"  Sites in main DB: {cnt_in_main} (Expected: 0)")
        assert cnt_in_main == 0, "No new sites should be saved to main DB!"
    else:
        print("  Sites table does not exist in main DB (OK)")

    # 3. Check sites inside event A database
    conn_a = sqlite3.connect(str(db_a_path))
    conn_a.row_factory = sqlite3.Row
    sites_a = conn_a.execute("SELECT * FROM sites").fetchall()
    print(f"  Sites in Event A DB: {[r['id'] for r in sites_a]} (Expected: ['SITE-A1'])")
    assert len(sites_a) == 1 and sites_a[0]["id"] == "SITE-A1", "SITE-A1 must be in Event A DB!"

    # 4. Check sites inside event B database
    conn_b = sqlite3.connect(str(db_b_path))
    conn_b.row_factory = sqlite3.Row
    sites_b = conn_b.execute("SELECT * FROM sites").fetchall()
    print(f"  Sites in Event B DB: {[r['id'] for r in sites_b]} (Expected: ['SITE-B1'])")
    assert len(sites_b) == 1 and sites_b[0]["id"] == "SITE-B1", "SITE-B1 must be in Event B DB!"

    # 5. Insert KPI measurements
    print("\nInserting KPI measurements...")
    db.insert_kpi_batch([
        {"site_id": "SITE-A1", "cell_id": "CELL-A1-1", "event_id": event_a_id, "timestamp": "2026-05-26T12:00:00Z", "metric": "utilization_dl", "value": 85.5}
    ])
    db.insert_kpi_batch([
        {"site_id": "SITE-B1", "cell_id": "CELL-B1-1", "event_id": event_b_id, "timestamp": "2026-05-26T12:00:00Z", "metric": "utilization_dl", "value": 42.0}
    ])

    # 6. Verify KPI measurements are isolated
    kpis_a = conn_a.execute("SELECT * FROM kpi_measurements").fetchall()
    print(f"  KPIs in Event A DB: {len(kpis_a)} (Expected: 1, value: {kpis_a[0]['value'] if kpis_a else None})")
    assert len(kpis_a) == 1 and kpis_a[0]["value"] == 85.5, "Expected 85.5 in Event A DB"

    kpis_b = conn_b.execute("SELECT * FROM kpi_measurements").fetchall()
    print(f"  KPIs in Event B DB: {len(kpis_b)} (Expected: 1, value: {kpis_b[0]['value'] if kpis_b else None})")
    assert len(kpis_b) == 1 and kpis_b[0]["value"] == 42.0, "Expected 42.0 in Event B DB"

    # Close connections
    conn_a.close()
    conn_b.close()
    db.close_conn()

    # 7. Clean up test files
    print("\nCleaning up test events and database files...")
    main_conn = db.get_conn()
    main_conn.execute("DELETE FROM events WHERE id IN (?, ?)", (event_a_id, event_b_id))
    main_conn.commit()
    db.close_conn()

    if db_a_path.exists():
        os.remove(db_a_path)
    if db_b_path.exists():
        os.remove(db_b_path)

    print("Verification completed successfully!")

if __name__ == "__main__":
    verify_isolation()
