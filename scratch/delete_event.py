import sys
from pathlib import Path
import sqlite3

# Define database path
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "smart_events.db"

def delete_event(event_id: str):
    if not DB_PATH.exists():
        print("Database does not exist.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    try:
        # Check if event exists
        cursor.execute("SELECT id, name FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        if not row:
            print(f"Event '{event_id}' not found in database.")
            return

        print(f"Deleting event: ID='{row[0]}', Name='{row[1]}'")

        # Explicitly delete from associated tables to be absolutely safe
        tables = [
            ("kpi_measurements", "event_id"),
            ("vip_measurements", "event_id"),
            ("alerts", "event_id"),
            ("event_vips", "event_id"),
            ("sites", "event_id"),
            ("events", "id")
        ]

        for table, col in tables:
            cursor.execute(f"DELETE FROM {table} WHERE {col} = ?", (event_id,))
            print(f"  - Deleted {cursor.rowcount} rows from {table}")

        conn.commit()
        print("Successfully deleted event and all its associated data.")

        # Run VACUUM
        cursor.execute("VACUUM")
        print("Database vacuumed.")

    except Exception as e:
        conn.rollback()
        print(f"Error deleting event: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    # We want to delete the example event 'gp-sp-2025-ended'
    delete_event("gp-sp-2025-ended")
