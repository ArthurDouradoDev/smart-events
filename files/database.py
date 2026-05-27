import sqlite3
import json
import threading
from pathlib import Path
from typing import List, Optional
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "smart_events.db"

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """Retorna conexão thread-local com o banco."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def close_conn():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None


def init_db():
    """Cria as tabelas se não existirem."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'SCHEDULED',
            start_time  TEXT,
            end_time    TEXT,
            polygon     TEXT,
            config_json TEXT
        );

        CREATE TABLE IF NOT EXISTS sites (
            id            TEXT,
            event_id      TEXT,
            name          TEXT,
            lat           REAL,
            lng           REAL,
            is_event_site INTEGER DEFAULT 1,
            cells_json    TEXT,
            PRIMARY KEY (id, event_id),
            FOREIGN KEY (event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS kpi_measurements (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id   TEXT NOT NULL,
            cell_id   TEXT NOT NULL,
            event_id  TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            metric    TEXT NOT NULL,
            value     REAL
        );

        CREATE INDEX IF NOT EXISTS idx_kpi_site_time
            ON kpi_measurements(site_id, metric, timestamp);

        CREATE TABLE IF NOT EXISTS vip_measurements (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vip_name     TEXT NOT NULL,
            event_id     TEXT NOT NULL,
            timestamp    TEXT NOT NULL,
            serving_cell TEXT,
            rsrp         REAL,
            rsrq         REAL,
            in_event     INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_vip_name_time
            ON vip_measurements(vip_name, timestamp);

        CREATE TABLE IF NOT EXISTS alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id     TEXT,
            level        TEXT,
            severity     TEXT,
            site_id      TEXT,
            cell_id      TEXT,
            message      TEXT,
            timestamp    TEXT,
            acknowledged INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS silenced_alerts (
            alert_key TEXT PRIMARY KEY
        );
    """)
    conn.commit()


# ── Events ──────────────────────────────────────────────────────────

def save_event(config_dict: dict):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO events
            (id, name, status, start_time, end_time, polygon, config_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        config_dict["id"],
        config_dict["name"],
        config_dict.get("status", "SCHEDULED"),
        config_dict.get("start_time"),
        config_dict.get("end_time"),
        json.dumps(config_dict.get("polygon", [])),
        json.dumps(config_dict),
    ))
    conn.commit()


def update_event_status(event_id: str, status: str):
    conn = get_conn()
    conn.execute(
        "UPDATE events SET status = ? WHERE id = ?", (status, event_id)
    )
    conn.commit()


def get_events(status: Optional[str] = None) -> List[dict]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM events WHERE status = ? ORDER BY start_time", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY start_time"
        ).fetchall()
    return [dict(r) for r in rows]


def get_event(event_id: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT config_json FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row:
        return json.loads(row["config_json"])
    return None


# ── KPI Measurements ────────────────────────────────────────────────

def insert_kpi_batch(measurements: List[dict]):
    """Insere lote de medições de KPI."""
    conn = get_conn()
    conn.executemany("""
        INSERT INTO kpi_measurements
            (site_id, cell_id, event_id, timestamp, metric, value)
        VALUES (:site_id, :cell_id, :event_id, :timestamp, :metric, :value)
    """, measurements)
    conn.commit()


def get_kpi_series(
    event_id: str,
    site_id: str,
    metric: str,
    minutes: int = 60
) -> List[dict]:
    """Retorna série temporal de uma métrica para um site."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT cell_id, timestamp, value
        FROM kpi_measurements
        WHERE event_id = ?
          AND site_id  = ?
          AND metric   = ?
          AND timestamp >= datetime('now', ? || ' minutes')
        ORDER BY timestamp ASC
    """, (event_id, site_id, metric, f"-{minutes}")).fetchall()
    return [dict(r) for r in rows]


def get_latest_kpi(event_id: str) -> List[dict]:
    """Retorna última medição de cada célula para calcular status dos sites."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT k.site_id, k.cell_id, k.metric, k.value, k.timestamp
        FROM kpi_measurements k
        INNER JOIN (
            SELECT site_id, cell_id, metric, MAX(timestamp) AS max_ts
            FROM kpi_measurements
            WHERE event_id = ?
            GROUP BY site_id, cell_id, metric
        ) latest ON k.site_id = latest.site_id
                    AND k.cell_id = latest.cell_id
                    AND k.metric  = latest.metric
                    AND k.timestamp = latest.max_ts
        WHERE k.event_id = ?
    """, (event_id, event_id)).fetchall()
    return [dict(r) for r in rows]


# ── VIP Measurements ────────────────────────────────────────────────

def insert_vip_batch(measurements: List[dict]):
    conn = get_conn()
    conn.executemany("""
        INSERT INTO vip_measurements
            (vip_name, event_id, timestamp, serving_cell, rsrp, rsrq, in_event)
        VALUES (:vip_name, :event_id, :timestamp, :serving_cell, :rsrp, :rsrq, :in_event)
    """, measurements)
    conn.commit()


def get_vip_latest(event_id: str) -> List[dict]:
    """Retorna última leitura de cada VIP."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT v.*
        FROM vip_measurements v
        INNER JOIN (
            SELECT vip_name, MAX(timestamp) AS max_ts
            FROM vip_measurements
            WHERE event_id = ?
            GROUP BY vip_name
        ) latest ON v.vip_name = latest.vip_name
                 AND v.timestamp = latest.max_ts
        WHERE v.event_id = ?
    """, (event_id, event_id)).fetchall()
    return [dict(r) for r in rows]


def get_vip_series(event_id: str, vip_name: str, minutes: int = 60) -> List[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT timestamp, rsrp, rsrq, serving_cell, in_event
        FROM vip_measurements
        WHERE event_id = ? AND vip_name = ?
          AND timestamp >= datetime('now', ? || ' minutes')
        ORDER BY timestamp ASC
    """, (event_id, vip_name, f"-{minutes}")).fetchall()
    return [dict(r) for r in rows]


# ── Alerts ──────────────────────────────────────────────────────────

def insert_alert(alert: dict) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO alerts
            (event_id, level, severity, site_id, cell_id, message, timestamp)
        VALUES (:event_id, :level, :severity, :site_id, :cell_id, :message, :timestamp)
    """, alert)
    conn.commit()
    return cur.lastrowid


def get_active_alerts(event_id: str) -> List[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM alerts
        WHERE event_id = ? AND acknowledged = 0
        ORDER BY timestamp DESC
    """, (event_id,)).fetchall()
    return [dict(r) for r in rows]


def acknowledge_alert(alert_id: int):
    conn = get_conn()
    conn.execute(
        "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,)
    )
    conn.commit()


def silence_alert(alert_key: str):
    """Silencia tipo de alerta para esta sessão."""
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO silenced_alerts VALUES (?)", (alert_key,)
    )
    conn.commit()


def is_silenced(alert_key: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM silenced_alerts WHERE alert_key = ?", (alert_key,)
    ).fetchone()
    return row is not None


def get_db_size_mb() -> float:
    if DB_PATH.exists():
        return round(DB_PATH.stat().st_size / (1024 * 1024), 1)
    return 0.0
