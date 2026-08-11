import logging
import sqlite3
import json
import threading
import sys
from pathlib import Path
from typing import List, Optional
from datetime import datetime

_db_logger = logging.getLogger(__name__)

# Rastreia quais eventos já tiveram dados de VIP migrados para o banco global nesta sessão.
# Garante que a migração rode apenas uma vez por evento por processo Python.
_migrated_events: set = set()

# Determina o diretório base para dados persistentes (próximo ao executável ou no workspace)
if getattr(sys, 'frozen', False):
    # Executando a partir do binário compilado pelo PyInstaller
    BASE_DIR = Path(sys.executable).parent
else:
    # Executando em modo de desenvolvimento (código fonte)
    BASE_DIR = Path(__file__).parent.parent

DB_PATH = BASE_DIR / "data" / "smart_events.db"

_local = threading.local()

_requests_lib = None
def _get_requests():
    global _requests_lib
    if _requests_lib is None:
        import sys
        if getattr(sys, "frozen", False):
            # No .exe não há shadowing do workspace; remover _MEIPASS do sys.path quebraria o import.
            import requests as req
            _requests_lib = req
        else:
            from pathlib import Path
            orig_path = list(sys.path)
            try:
                cwd_resolved = Path.cwd().resolve()
                parent_resolved = Path(__file__).parent.parent.resolve()
                sys.path = [p for p in sys.path if p and Path(p).resolve() not in (cwd_resolved, parent_resolved)]
                import requests as req
                _requests_lib = req
            finally:
                sys.path = orig_path
    return _requests_lib


_event_local = threading.local()

def get_event_db_path(event_id: str) -> Path:
    safe_id = "".join([c for c in event_id if c.isalnum() or c in ("-", "_")]).strip()
    return BASE_DIR / "data" / f"smart_events_{safe_id}.db"

def init_event_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
            id            TEXT,
            event_id      TEXT,
            name          TEXT,
            lat           REAL,
            lng           REAL,
            is_event_site INTEGER DEFAULT 1,
            cells_json    TEXT,
            PRIMARY KEY (id, event_id)
        );

        CREATE TABLE IF NOT EXISTS kpi_measurements (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id   TEXT NOT NULL,
            cell_id   TEXT NOT NULL,
            event_id  TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            metric    TEXT NOT NULL,
            value     REAL,
            scope     TEXT NOT NULL DEFAULT 'CELL',
            technology TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_kpi_site_time
            ON kpi_measurements(site_id, metric, timestamp);

        CREATE TABLE IF NOT EXISTS collection_checkpoints (
            event_id   TEXT NOT NULL,
            oss        TEXT NOT NULL DEFAULT '',
            collector  TEXT NOT NULL,
            task_id    TEXT NOT NULL,
            object_key TEXT NOT NULL DEFAULT '',
            cursor     TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (event_id, oss, collector, task_id, object_key)
        );

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

        CREATE TABLE IF NOT EXISTS event_vips (
            event_id TEXT NOT NULL,
            vip_id   TEXT NOT NULL,
            task_id  INTEGER,
            PRIMARY KEY (event_id, vip_id)
        );

        CREATE TABLE IF NOT EXISTS alarms (
            csn             INTEGER PRIMARY KEY,      -- id único da ocorrência (dedup natural)
            event_id        TEXT NOT NULL,
            alarm_id        TEXT,
            alarm_group_id  TEXT,
            alarm_name      TEXT,
            severity        TEXT,
            source          TEXT,                     -- meName (ex.: SR-UWCTJ1)
            ip              TEXT,
            location        TEXT,
            occur_time      TEXT,
            arrive_time     TEXT,
            additional_info TEXT,
            collected_at    TEXT NOT NULL             -- quando o app coletou (p/ timeline)
        );

        CREATE INDEX IF NOT EXISTS idx_alarms_arrive ON alarms(arrive_time);
        CREATE INDEX IF NOT EXISTS idx_alarms_name ON alarms(alarm_name);
    """)
    # Bancos de eventos criados antes da Fase 2 não possuem ``scope``. A
    # migração é aditiva e mantém os históricos como linhas de célula.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(kpi_measurements)")}
    if "scope" not in columns:
        conn.execute("ALTER TABLE kpi_measurements ADD COLUMN scope TEXT NOT NULL DEFAULT 'CELL'")
    if "technology" not in columns:
        conn.execute("ALTER TABLE kpi_measurements ADD COLUMN technology TEXT NOT NULL DEFAULT ''")
    # Replays são esperados após uma queda antes de salvar o cursor. Conserva a
    # primeira ocorrência de qualquer duplicata histórica e torna o replay seguro.
    conn.execute("""
        DELETE FROM kpi_measurements
        WHERE id NOT IN (
            SELECT MIN(id) FROM kpi_measurements
            GROUP BY event_id, site_id, cell_id, timestamp, metric, scope, technology
        )
    """)
    conn.execute("DROP INDEX IF EXISTS uq_kpi_measurement_replay")
    conn.execute("""
        CREATE UNIQUE INDEX uq_kpi_measurement_replay
        ON kpi_measurements(event_id, site_id, cell_id, timestamp, metric, scope, technology)
    """)
    conn.commit()

def get_event_conn(event_id: str) -> sqlite3.Connection:
    if not event_id:
        return get_conn()
    attr_name = f"conn_{event_id}"
    if not hasattr(_event_local, attr_name) or getattr(_event_local, attr_name) is None:
        db_path = get_event_db_path(event_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        init_event_db(conn)
        setattr(_event_local, attr_name, conn)
    return getattr(_event_local, attr_name)


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
    for attr in list(_event_local.__dict__.keys()):
        conn = getattr(_event_local, attr)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        setattr(_event_local, attr, None)


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

        -- Cadastro global de VIPs (reaproveitado entre eventos)
        CREATE TABLE IF NOT EXISTS vips (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            role       TEXT,
            notes      TEXT,
            task_id    INTEGER,
            oss        TEXT,
            updated_at TEXT
        );

        -- Associação VIP↔Evento com a task_id específica do evento
        CREATE TABLE IF NOT EXISTS event_vips (
            event_id TEXT NOT NULL,
            vip_id   TEXT NOT NULL,
            task_id  INTEGER,
            PRIMARY KEY (event_id, vip_id),
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY (vip_id)   REFERENCES vips(id)   ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_event_vips_event
            ON event_vips(event_id);
    """)
    conn.commit()

    # Adiciona a coluna task_id na tabela vips se ela não existir (migração)
    try:
        conn.execute("ALTER TABLE vips ADD COLUMN task_id INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Adiciona a coluna oss na tabela vips se ela não existir (migração)
    try:
        conn.execute("ALTER TABLE vips ADD COLUMN oss TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Adiciona a coluna cliente na tabela vips (nível acima da regional: TIM, Vivo, …)
    try:
        conn.execute("ALTER TABLE vips ADD COLUMN cliente TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Garante UNIQUE index em vip_measurements global para deduplicação entre migrações.
    # Se já houver duplicatas (ciclos anteriores sem o índice), remove-as primeiro
    # mantendo o registro de maior id para cada (vip_name, timestamp).
    try:
        conn.execute("""
            DELETE FROM vip_measurements
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM vip_measurements
                GROUP BY vip_name, timestamp
            )
        """)
        conn.commit()
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_vip_dedup "
            "ON vip_measurements(vip_name, timestamp)"
        )
        conn.commit()
    except Exception:
        pass


# ── Events ──────────────────────────────────────────────────────────

def save_event(config_dict: dict):
    conn = get_conn()
    event_id = config_dict["id"]
    conn.execute("""
        INSERT OR REPLACE INTO events
            (id, name, status, start_time, end_time, polygon, config_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        config_dict["name"],
        config_dict.get("status", "SCHEDULED"),
        config_dict.get("start_time"),
        config_dict.get("end_time"),
        json.dumps(config_dict.get("polygon", [])),
        json.dumps(config_dict),
    ))
    conn.commit()
    
    # Salvar sites associados na tabela sites (no banco específico do evento)
    e_conn = get_event_conn(event_id)
    for site in config_dict.get("sites", []):
        e_conn.execute("""
            INSERT OR REPLACE INTO sites (id, event_id, name, lat, lng, is_event_site, cells_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            site["id"],
            event_id,
            site["name"],
            site["lat"],
            site["lng"],
            1 if site.get("is_event_site", True) else 0,
            json.dumps(site.get("cells", []))
        ))

    # Sincroniza associações VIP↔evento a partir do JSON (no banco específico do evento)
    raw_vips = config_dict.get("vips", []) or []
    if raw_vips:
        e_conn.execute("DELETE FROM event_vips WHERE event_id = ?", (event_id,))
        normalized = []
        for v in raw_vips:
            vip_id = v.get("id")
            if not vip_id and v.get("name"):
                vip_id = slugify(v["name"])
                # garante VIP global existe
                existing = conn.execute("SELECT id FROM vips WHERE id = ?", (vip_id,)).fetchone()
                if not existing:
                    conn.execute("""
                        INSERT INTO vips (id, name, role, notes, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (vip_id, v["name"], v.get("role"), v.get("notes"),
                          datetime.utcnow().isoformat()))
                    conn.commit()
            if not vip_id:
                continue
            vip_row = conn.execute("SELECT id FROM vips WHERE id = ?", (vip_id,)).fetchone()
            if not vip_row:
                conn.execute(
                    "INSERT INTO vips (id, name, role, notes, updated_at) VALUES (?, ?, NULL, NULL, ?)",
                    (vip_id, vip_id, datetime.utcnow().isoformat())
                )
                conn.commit()
            task_id = v.get("task_id")
            e_conn.execute("""
                INSERT OR REPLACE INTO event_vips (event_id, vip_id, task_id)
                VALUES (?, ?, ?)
            """, (event_id, vip_id, task_id))
            normalized.append({"id": vip_id, "task_id": task_id})

        # Reescreve o config_json com o formato novo (sem name/imsi embarcados)
        if normalized != raw_vips:
            new_config = dict(config_dict)
            new_config["vips"] = normalized
            conn.execute(
                "UPDATE events SET config_json = ? WHERE id = ?",
                (json.dumps(new_config), event_id)
            )
            conn.commit()

    e_conn.commit()


def update_event_status(event_id: str, status: str):
    conn = get_conn()
    row = conn.execute("SELECT config_json FROM events WHERE id = ?", (event_id,)).fetchone()
    if row:
        try:
            config = json.loads(row["config_json"])
            config["status"] = status
            conn.execute(
                "UPDATE events SET status = ?, config_json = ? WHERE id = ?",
                (status, json.dumps(config), event_id)
            )
        except Exception:
            conn.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
    else:
        conn.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
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
        "SELECT config_json, status FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row:
        try:
            config = json.loads(row["config_json"])
            config["status"] = row["status"]
            return config
        except Exception:
            pass
    return None


def delete_event(event_id: str):
    """Remove um evento do banco global e apaga o banco específico do evento (medições)."""
    conn = get_conn()
    # Remove linhas-filhas no banco global antes do evento (sites tem FK para events).
    for tbl in ("sites", "event_vips", "kpi_measurements", "vip_measurements", "alerts"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE event_id = ?", (event_id,))
        except Exception:
            pass
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()

    # Fecha a conexão thread-local deste evento (se aberta neste thread) antes de apagar o arquivo.
    attr_name = f"conn_{event_id}"
    c = getattr(_event_local, attr_name, None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        try:
            delattr(_event_local, attr_name)
        except Exception:
            pass
    try:
        path = get_event_db_path(event_id)
        if path.exists():
            path.unlink()
    except Exception as e:
        _db_logger.warning(f"Não foi possível remover o banco do evento {event_id}: {e}")


# ── KPI Measurements ────────────────────────────────────────────────

def insert_kpi_batch(measurements: List[dict]):
    """Insere KPIs idempotentemente e devolve inseridos/duplicados."""
    if not measurements:
        return {"inserted": 0, "duplicate": 0}
    event_id = measurements[0]["event_id"]
    conn = get_event_conn(event_id)
    rows = [{**item, "scope": item.get("scope", "CELL"), "technology": item.get("technology", "")} for item in measurements]
    before = conn.total_changes
    conn.executemany("""
        INSERT INTO kpi_measurements
            (site_id, cell_id, event_id, timestamp, metric, value, scope, technology)
        VALUES (:site_id, :cell_id, :event_id, :timestamp, :metric, :value, :scope, :technology)
        ON CONFLICT(event_id, site_id, cell_id, timestamp, metric, scope, technology) DO NOTHING
    """, rows)
    conn.commit()
    inserted = conn.total_changes - before
    return {"inserted": inserted, "duplicate": len(rows) - inserted}


def get_collection_checkpoints(event_id: str, collector: str, task_id, oss: str = "") -> dict[str, str]:
    """Retorna cursores confirmados, isolados por evento/OSS/task/objeto."""
    conn = get_event_conn(event_id)
    rows = conn.execute("""
        SELECT object_key, cursor FROM collection_checkpoints
        WHERE event_id = ? AND oss = ? AND collector = ? AND task_id = ?
    """, (event_id, oss or "", collector, str(task_id))).fetchall()
    return {row["object_key"]: row["cursor"] for row in rows}


def save_collection_checkpoints(event_id: str, cursors: dict, collector: str = "monitoring", oss: str = "") -> None:
    """Persiste checkpoints somente quando chamado após gravar o lote correspondente.

    ``cursors`` aceita ``{(task_id, object_key): cursor}`` ou a forma serializável
    ``{"task:obj": {"task_id": ..., "object_key": ..., "cursor": ...}}``.
    """
    if not cursors:
        return
    conn = get_event_conn(event_id)
    now = datetime.utcnow().isoformat() + "Z"
    rows = []
    for key, value in cursors.items():
        if isinstance(value, dict):
            task_id = value.get("task_id")
            object_key = value.get("object_key", "")
            cursor = value.get("cursor")
        elif isinstance(key, tuple) and len(key) == 2:
            task_id, object_key, cursor = key[0], key[1], value
        else:
            continue
        if task_id is None or cursor is None:
            continue
        rows.append((event_id, oss or "", collector, str(task_id), str(object_key or ""), str(cursor), now))
    conn.executemany("""
        INSERT INTO collection_checkpoints(event_id, oss, collector, task_id, object_key, cursor, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, oss, collector, task_id, object_key)
        DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
    """, rows)
    conn.commit()


def get_kpi_series(
    event_id: str,
    site_id: str,
    metric: str,
    minutes: int = 60
) -> List[dict]:
    """Retorna série temporal de uma métrica para um site."""
    conn = get_event_conn(event_id)
    if minutes and minutes > 0:
        rows = conn.execute("""
            SELECT cell_id, timestamp, value
            FROM kpi_measurements
            WHERE event_id = ?
              AND site_id  = ?
              AND metric   = ? AND scope = 'CELL'
              AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')
            ORDER BY timestamp ASC
        """, (event_id, site_id, metric, f"-{minutes}")).fetchall()
    else:
        rows = conn.execute("""
            SELECT cell_id, timestamp, value
            FROM kpi_measurements
            WHERE event_id = ?
              AND site_id  = ?
              AND metric   = ? AND scope = 'CELL'
            ORDER BY timestamp ASC
        """, (event_id, site_id, metric)).fetchall()
    return [dict(r) for r in rows]


def get_kpi_series_by_cell(
    event_id: str,
    site_id: str,
    cell_id: str,
    metric: str,
    minutes: int = 60
) -> List[dict]:
    """
    Retorna série temporal de uma métrica para uma célula específica.
    `cell_id == '__all__'` retorna todas as células (comportamento de get_kpi_series).
    """
    conn = get_event_conn(event_id)
    if cell_id == "__all__":
        return get_kpi_series(event_id, site_id, metric, minutes)

    if minutes and minutes > 0:
        rows = conn.execute("""
            SELECT cell_id, timestamp, value
            FROM kpi_measurements
            WHERE event_id = ?
              AND site_id  = ?
              AND cell_id  = ?
              AND metric   = ? AND scope = 'CELL'
              AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')
            ORDER BY timestamp ASC
        """, (event_id, site_id, cell_id, metric, f"-{minutes}")).fetchall()
    else:
        rows = conn.execute("""
            SELECT cell_id, timestamp, value
            FROM kpi_measurements
            WHERE event_id = ?
              AND site_id  = ?
              AND cell_id  = ?
              AND metric   = ? AND scope = 'CELL'
            ORDER BY timestamp ASC
        """, (event_id, site_id, cell_id, metric)).fetchall()
    return [dict(r) for r in rows]


def get_kpi_site_series(event_id: str, site_id: str, metric: str, minutes: int = 60,
                        technology: str | None = None) -> List[dict]:
    """Série já agregada e persistida para ``Site completo`` (nunca média ad-hoc)."""
    conn = get_event_conn(event_id)
    conditions = ["event_id = ?", "site_id = ?", "metric = ?", "scope = 'SITE'"]
    params = [event_id, site_id, metric]
    if technology:
        conditions.append("technology = ?")
        params.append(technology)
    if minutes and minutes > 0:
        conditions.append("timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')")
        params.append(f"-{minutes}")
    rows = conn.execute(
        "SELECT cell_id, timestamp, value, technology FROM kpi_measurements WHERE "
        + " AND ".join(conditions) + " ORDER BY timestamp ASC", params).fetchall()
    return [dict(row) for row in rows]


def get_latest_site_kpi_by_metric(event_id: str, metric: str, max_timestamp: Optional[str] = None) -> List[dict]:
    """Última linha SITE de cada site/tecnologia para listas e mapas."""
    conn = get_event_conn(event_id)
    ts_sql = " AND timestamp <= ?" if max_timestamp else ""
    params = [event_id, metric] + ([max_timestamp] if max_timestamp else []) + [event_id, metric]
    rows = conn.execute(f"""
        SELECT k.site_id, k.cell_id, k.metric, k.value, k.timestamp, k.technology, k.scope
        FROM kpi_measurements k
        INNER JOIN (
            SELECT site_id, technology, MAX(timestamp) max_ts
            FROM kpi_measurements
            WHERE event_id = ? AND metric = ? AND scope = 'SITE' {ts_sql}
            GROUP BY site_id, technology
        ) latest ON latest.site_id = k.site_id AND latest.technology = k.technology AND latest.max_ts = k.timestamp
        WHERE k.event_id = ? AND k.metric = ? AND k.scope = 'SITE'
    """, params).fetchall()
    return [dict(row) for row in rows]


def get_latest_kpi_by_metric(
    event_id: str,
    metric: str,
    max_timestamp: Optional[str] = None
) -> List[dict]:
    """
    Retorna última medição de cada (site_id, cell_id) para uma métrica específica.
    Usado para calcular o valor contextual exibido na lista de sites.
    """
    conn = get_event_conn(event_id)
    ts_filter = "AND k.timestamp <= ?" if max_timestamp else ""
    params_inner = [event_id, metric]
    if max_timestamp:
        params_inner.append(max_timestamp)
    params_inner.append(event_id)

    rows = conn.execute(f"""
        SELECT k.site_id, k.cell_id, k.metric, k.value, k.timestamp
        FROM kpi_measurements k
        INNER JOIN (
            SELECT site_id, cell_id, MAX(timestamp) AS max_ts
            FROM kpi_measurements
            WHERE event_id = ? AND metric = ? {ts_filter}
            GROUP BY site_id, cell_id
        ) latest ON k.site_id = latest.site_id
                    AND k.cell_id = latest.cell_id
                    AND k.timestamp = latest.max_ts
        WHERE k.event_id = ? AND k.metric = ?
    """, params_inner + [metric]).fetchall()
    return [dict(r) for r in rows]


def get_latest_kpi(event_id: str, max_timestamp: Optional[str] = None) -> List[dict]:
    """Retorna última medição de cada célula para calcular status dos sites (opcionalmente filtrada até max_timestamp)."""
    conn = get_event_conn(event_id)
    if max_timestamp:
        rows = conn.execute("""
            SELECT k.site_id, k.cell_id, k.metric, k.value, k.timestamp
            FROM kpi_measurements k
            INNER JOIN (
                SELECT site_id, cell_id, metric, MAX(timestamp) AS max_ts
                FROM kpi_measurements
                WHERE event_id = ? AND timestamp <= ?
                GROUP BY site_id, cell_id, metric
            ) latest ON k.site_id = latest.site_id
                        AND k.cell_id = latest.cell_id
                        AND k.metric  = latest.metric
                        AND k.timestamp = latest.max_ts
            WHERE k.event_id = ?
        """, (event_id, max_timestamp, event_id)).fetchall()
    else:
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
    """Insere medições de VIP no banco global (deduplicação por vip_name + timestamp)."""
    if not measurements:
        return
    conn = get_conn()  # banco global — VIP measurements são independentes de evento
    conn.executemany("""
        INSERT OR IGNORE INTO vip_measurements
            (vip_name, event_id, timestamp, serving_cell, rsrp, rsrq, in_event)
        VALUES (:vip_name, :event_id, :timestamp, :serving_cell, :rsrp, :rsrq, :in_event)
    """, measurements)
    conn.commit()


def get_vip_latest(max_timestamp: Optional[str] = None) -> List[dict]:
    """
    Retorna a medição mais recente de cada VIP do banco global.
    - Live (max_timestamp=None): medição mais recente independente de evento.
    - Histórico (max_timestamp set): medição mais recente até aquele instante.
    """
    conn = get_conn()
    if max_timestamp:
        rows = conn.execute("""
            SELECT v.*
            FROM vip_measurements v
            INNER JOIN (
                SELECT vip_name, MAX(timestamp) AS max_ts
                FROM vip_measurements
                WHERE timestamp <= ?
                GROUP BY vip_name
            ) latest ON v.vip_name = latest.vip_name
                     AND v.timestamp = latest.max_ts
        """, (max_timestamp,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT v.*
            FROM vip_measurements v
            INNER JOIN (
                SELECT vip_name, MAX(timestamp) AS max_ts
                FROM vip_measurements
                GROUP BY vip_name
            ) latest ON v.vip_name = latest.vip_name
                     AND v.timestamp = latest.max_ts
        """).fetchall()
    return [dict(r) for r in rows]


def get_vip_series(event_id: str, vip_name: str, minutes: int = 60) -> List[dict]:
    """
    Retorna série temporal de RSRP/RSRQ de um VIP do banco global.
    event_id é mantido na assinatura para compatibilidade mas não filtra os dados —
    VIP measurements são globais (independentes de evento).
    Fallback: se a janela temporal retornar vazio, retorna os últimos 120 registros.
    """
    conn = get_conn()  # banco global
    # Tentativa 1: janela temporal normal
    rows = conn.execute("""
        SELECT timestamp, rsrp, rsrq, serving_cell, in_event
        FROM vip_measurements
        WHERE vip_name = ?
          AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')
        ORDER BY timestamp ASC
    """, (vip_name, f"-{minutes}")).fetchall()

    if rows:
        return [dict(r) for r in rows]

    # Fallback: sem filtro temporal — retorna os últimos 120 registros disponíveis
    rows = conn.execute("""
        SELECT timestamp, rsrp, rsrq, serving_cell, in_event
        FROM vip_measurements
        WHERE vip_name = ?
        ORDER BY timestamp DESC
        LIMIT 120
    """, (vip_name,)).fetchall()
    return [dict(r) for r in reversed(rows)]  # retorna em ordem crescente


# ── Alarms (iMaster FM website, filtrados por tipo) ─────────────────

def insert_alarms_batch(measurements: List[dict]):
    """Insere lote de alarmes no banco do evento. INSERT OR IGNORE por csn (dedup)."""
    if not measurements:
        return
    event_id = measurements[0]["event_id"]
    conn = get_event_conn(event_id)
    conn.executemany("""
        INSERT OR IGNORE INTO alarms
            (csn, event_id, alarm_id, alarm_group_id, alarm_name, severity,
             source, ip, location, occur_time, arrive_time, additional_info, collected_at)
        VALUES (:csn, :event_id, :alarm_id, :alarm_group_id, :alarm_name, :severity,
                :source, :ip, :location, :occur_time, :arrive_time, :additional_info, :collected_at)
    """, measurements)
    conn.commit()


def get_alarms(event_id: str, timestamp: Optional[str] = None, limit: int = 500) -> List[dict]:
    """Lista alarmes do evento ordenados por arrive_time DESC.

    Com `timestamp` (modo histórico/timeline) filtra collected_at <= timestamp,
    coerente com get_sites/get_vips."""
    conn = get_event_conn(event_id)
    if timestamp:
        rows = conn.execute("""
            SELECT * FROM alarms
            WHERE event_id = ? AND collected_at <= ?
            ORDER BY arrive_time DESC
            LIMIT ?
        """, (event_id, timestamp, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM alarms
            WHERE event_id = ?
            ORDER BY arrive_time DESC
            LIMIT ?
        """, (event_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ── Alerts ──────────────────────────────────────────────────────────

def insert_alert(alert: dict) -> int:
    event_id = alert["event_id"]
    conn = get_event_conn(event_id)
    cur = conn.execute("""
        INSERT INTO alerts
            (event_id, level, severity, site_id, cell_id, message, timestamp)
        VALUES (:event_id, :level, :severity, :site_id, :cell_id, :message, :timestamp)
    """, alert)
    conn.commit()
    return cur.lastrowid


def get_active_alerts(event_id: str, max_timestamp: Optional[str] = None) -> List[dict]:
    conn = get_event_conn(event_id)
    if max_timestamp:
        rows = conn.execute("""
            SELECT * FROM alerts
            WHERE event_id = ? AND acknowledged = 0 AND timestamp <= ?
            ORDER BY timestamp DESC
        """, (event_id, max_timestamp)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM alerts
            WHERE event_id = ? AND acknowledged = 0
            ORDER BY timestamp DESC
        """, (event_id,)).fetchall()
    return [dict(r) for r in rows]


def acknowledge_alert(event_id: str, alert_id: int):
    conn = get_event_conn(event_id)
    conn.execute(
        "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,)
    )
    conn.commit()


def acknowledge_all_alerts(event_id: str):
    conn = get_event_conn(event_id)
    conn.execute(
        "UPDATE alerts SET acknowledged = 1 WHERE event_id = ? AND acknowledged = 0", (event_id,)
    )
    conn.commit()


def delete_all_alerts(event_id: str):
    """Exclui todos os alertas do evento do banco de dados."""
    conn = get_event_conn(event_id)
    conn.execute("DELETE FROM alerts WHERE event_id = ?", (event_id,))
    conn.commit()


def get_all_alerts(event_id: str) -> List[dict]:
    """Retorna todos os alertas (ativos e reconhecidos) ordenados por timestamp."""
    conn = get_event_conn(event_id)
    rows = conn.execute("""
        SELECT * FROM alerts
        WHERE event_id = ?
        ORDER BY timestamp DESC
    """, (event_id,)).fetchall()
    return [dict(r) for r in rows]


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
    total_size = 0.0
    try:
        # Soma o tamanho de smart_events.db e smart_events_*.db
        data_dir = DB_PATH.parent
        for f in data_dir.glob("smart_events*.db"):
            total_size += f.stat().st_size
    except Exception:
        if DB_PATH.exists():
            total_size = DB_PATH.stat().st_size
    return round(total_size / (1024 * 1024), 1)


def get_event_timestamps(event_id: str) -> List[str]:
    """Retorna lista ordenada de todos os timestamps de medições para o evento.
    KPIs: banco específico do evento. VIPs: banco global filtrado por event_id."""
    e_conn = get_event_conn(event_id)
    kpi_rows = e_conn.execute(
        "SELECT DISTINCT timestamp FROM kpi_measurements WHERE event_id = ?", (event_id,)
    ).fetchall()

    g_conn = get_conn()
    vip_rows = g_conn.execute(
        "SELECT DISTINCT timestamp FROM vip_measurements WHERE event_id = ?", (event_id,)
    ).fetchall()

    timestamps = sorted(
        {r["timestamp"] for r in kpi_rows} | {r["timestamp"] for r in vip_rows}
    )
    return timestamps


# ── Configurações e Sincronização Compartilhada de Eventos ──────────────────

SETTINGS_PATH = BASE_DIR / "data" / "settings.json"


def get_settings() -> dict:
    """Carrega as configurações locais (settings.json). Cria o arquivo se não existir."""
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        default_settings = {
            "server_url": ""
        }
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(default_settings, f, indent=4, ensure_ascii=False)
        except Exception:
            pass
        return default_settings

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"server_url": ""}


def save_settings(settings: dict):
    """Salva as configurações locais."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving settings: {e}")


def sync_events_from_server() -> dict:
    """
    Sincroniza os eventos do servidor de API central para o banco SQLite local.
    Retorna estatísticas da sincronização: {'sincronizados': X, 'erros': Y}
    """
    settings = get_settings()
    server_url = settings.get("server_url", "").strip()
    if not server_url:
        return {"sincronizados": 0, "erros": 0, "msg": "Servidor não configurado."}
    
    requests = _get_requests()
    
    server_url = server_url.rstrip("/")
    url = f"{server_url}/api/events"
    
    sincronizados = 0
    erros = 0
    
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return {"sincronizados": 0, "erros": 1, "msg": f"Erro do servidor (HTTP {response.status_code})"}
        
        events = response.json()
        if not isinstance(events, list):
            return {"sincronizados": 0, "erros": 1, "msg": "Resposta do servidor em formato inválido."}

        server_ids = set()
        for config in events:
            try:
                # Validação básica do formato de configuração de evento
                if not isinstance(config, dict) or "id" not in config or "name" not in config:
                    continue
                server_ids.add(config["id"])

                # Preserva o status local se ele for mais avançado (ACTIVE, ENDED) do que o do JSON do servidor
                local_event = get_event(config["id"])
                if local_event:
                    local_status = local_event.get("status", "SCHEDULED")
                    remote_status = config.get("status", "SCHEDULED")
                    if local_status in ("ACTIVE", "ENDED") and remote_status == "SCHEDULED":
                        config["status"] = local_status

                save_event(config)
                sincronizados += 1
            except Exception as e:
                print(f"Erro ao sincronizar evento {config.get('id')}: {e}")
                erros += 1

        # Remove eventos locais que não existem mais no servidor (ex.: eventos de teste residuais).
        # O servidor (server_data) é a fonte da verdade no modo offline embutido.
        # SALVAGUARDA: só limpa se o servidor retornou pelo menos 1 evento — evita apagar tudo
        # (incl. dados do evento ativo) caso o servidor responda com lista vazia transitoriamente.
        removidos = 0
        if server_ids:
            try:
                for local in get_events():
                    if local["id"] not in server_ids:
                        delete_event(local["id"])
                        removidos += 1
                        _db_logger.info(f"Evento residual removido (ausente no servidor): {local['id']}")
            except Exception as e:
                _db_logger.warning(f"Falha na limpeza de eventos órfãos: {e}")
    except Exception as e:
        print(f"Erro ao ler servidor central: {e}")
        return {"sincronizados": 0, "erros": 1, "msg": str(e)}

    return {"sincronizados": sincronizados, "erros": erros, "removidos": removidos}


def export_event_to_server(config_dict: dict) -> bool:
    """
    Exporta a configuração de um evento local para o servidor de API central.
    """
    settings = get_settings()
    server_url = settings.get("server_url", "").strip()
    if not server_url:
        return False

    requests = _get_requests()

    server_url = server_url.rstrip("/")
    url = f"{server_url}/api/events"

    try:
        response = requests.post(url, json=config_dict, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"Erro ao exportar evento para o servidor central: {e}")
        return False


# ── VIPs (cadastro global) ──────────────────────────────────────────

def slugify(name: str) -> str:
    """
    Converte 'Carlos Menezes' → 'carlos-menezes'.
    Mantém apenas a-z 0-9 e traços; remove acentos via tradução básica.
    """
    import unicodedata, re
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "vip"


def save_vip(vip: dict) -> dict:
    """Cria ou atualiza um VIP global. Se o id não vier, gera a partir do nome."""
    conn = get_conn()
    vip_id = vip.get("id") or slugify(vip["name"])

    # Resolve colisão: se já existe um vip com esse id e nome diferente, sufixa.
    if not vip.get("id"):
        suffix = 2
        original_id = vip_id
        while True:
            existing = conn.execute("SELECT name FROM vips WHERE id = ?", (vip_id,)).fetchone()
            if not existing or existing["name"] == vip["name"]:
                break
            vip_id = f"{original_id}-{suffix}"
            suffix += 1

    conn.execute("""
        INSERT INTO vips (id, name, role, notes, task_id, oss, cliente, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            role = excluded.role,
            notes = excluded.notes,
            task_id = excluded.task_id,
            oss = excluded.oss,
            cliente = excluded.cliente,
            updated_at = excluded.updated_at
    """, (vip_id, vip["name"], vip.get("role"), vip.get("notes"), vip.get("task_id"), vip.get("oss"),
          vip.get("cliente"), datetime.utcnow().isoformat()))
    conn.commit()
    return get_vip(vip_id)


def get_vip(vip_id: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM vips WHERE id = ?", (vip_id,)).fetchone()
    return dict(row) if row else None


def get_vips() -> List[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM vips ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def delete_vip(vip_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM vips WHERE id = ?", (vip_id,))
    conn.commit()
    
    # Remove associações em todos os bancos específicos de evento
    try:
        for e in get_events():
            e_conn = get_event_conn(e["id"])
            e_conn.execute("DELETE FROM event_vips WHERE vip_id = ?", (vip_id,))
            e_conn.commit()
    except Exception as e:
        print(f"Erro ao remover associação do VIP {vip_id} nos eventos: {e}")
        
    return cur.rowcount > 0


def assign_vip_to_event(event_id: str, vip_id: str, task_id: Optional[int] = None):
    e_conn = get_event_conn(event_id)
    e_conn.execute("""
        INSERT OR REPLACE INTO event_vips (event_id, vip_id, task_id)
        VALUES (?, ?, ?)
    """, (event_id, vip_id, task_id))
    e_conn.commit()
    # Reflete a mudança no config_json (vips embarcado) pra manter export consistente.
    _resync_event_vips_into_config(event_id)


def unassign_vip_from_event(event_id: str, vip_id: str):
    e_conn = get_event_conn(event_id)
    e_conn.execute(
        "DELETE FROM event_vips WHERE event_id = ? AND vip_id = ?",
        (event_id, vip_id)
    )
    e_conn.commit()
    _resync_event_vips_into_config(event_id)


def get_event_vips(event_id: str) -> List[dict]:
    """Retorna os VIPs (cadastro global) filtrados pelo cliente + regional (OSS) do evento atual."""
    conn = get_conn()

    # Busca cliente e regional/OSS do evento
    oss = None
    cliente = None
    event = get_event(event_id)
    if event and isinstance(event.get("oss"), dict):
        oss = event["oss"].get("region")
        cliente = event["oss"].get("cliente")

    clauses, params = [], []
    if oss:
        clauses.append("oss = ?")
        params.append(oss)
    if cliente:
        # Compatível com VIPs legados sem cliente (cliente NULL/''): eles ainda aparecem;
        # só excluímos VIPs marcados com um cliente DIFERENTE.
        clauses.append("(cliente = ? OR cliente IS NULL OR cliente = '')")
        params.append(cliente)

    if clauses:
        sql = f"SELECT * FROM vips WHERE {' AND '.join(clauses)} ORDER BY name"
        vips = conn.execute(sql, params).fetchall()
    else:
        vips = conn.execute("SELECT * FROM vips ORDER BY name").fetchall()

    return [dict(v) for v in vips]


def _resync_event_vips_into_config(event_id: str):
    """Atualiza events.config_json.vips com pares {id, task_id} a partir de event_vips."""
    conn = get_conn()
    row = conn.execute("SELECT config_json FROM events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return
    try:
        config = json.loads(row["config_json"])
    except Exception:
        return
    e_conn = get_event_conn(event_id)
    pairs = e_conn.execute(
        "SELECT vip_id, task_id FROM event_vips WHERE event_id = ?",
        (event_id,)
    ).fetchall()
    config["vips"] = [{"id": r["vip_id"], "task_id": r["task_id"]} for r in pairs]
    conn.execute(
        "UPDATE events SET config_json = ? WHERE id = ?",
        (json.dumps(config), event_id)
    )
    conn.commit()


def sync_vips_from_server(oss: Optional[str] = None, cliente: Optional[str] = None) -> dict:
    """Sincroniza VIPs globais e suas associações com eventos a partir do servidor central.

    Filtra por regional (`oss`) e cliente (TIM, Vivo, …). Suporta dois formatos de resposta:
      - VIP com campo 'event_vips': [{event_id, task_id}] → associações embutidas
      - Endpoint separado GET /api/event-vips → [{event_id, vip_id, task_id}]
    """
    if not oss or not cliente:
        try:
            active_events = get_events(status="ACTIVE")
            if active_events:
                full_event = get_event(active_events[0]["id"])
                if full_event and isinstance(full_event.get("oss"), dict):
                    oss = oss or full_event["oss"].get("region")
                    cliente = cliente or full_event["oss"].get("cliente")
        except Exception as e:
            print(f"Erro ao tentar detectar cliente/regional do evento ativo: {e}")

    settings = get_settings()
    server_url = settings.get("server_url", "").strip()
    if not server_url:
        return {"sincronizados": 0, "erros": 0, "msg": "Servidor não configurado."}

    requests = _get_requests()

    base = server_url.rstrip("/")
    sincronizados = 0
    erros = 0
    try:
        url = f"{base}/api/vips"
        query = []
        if oss:
            query.append(f"oss={oss}")
        if cliente:
            query.append(f"cliente={cliente}")
        if query:
            url += "?" + "&".join(query)
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return {"sincronizados": 0, "erros": 1,
                    "msg": f"Erro do servidor (HTTP {response.status_code})"}
        vips = response.json()
        if not isinstance(vips, list):
            return {"sincronizados": 0, "erros": 1,
                    "msg": "Resposta do servidor em formato inválido."}
        for vip in vips:
            try:
                if not isinstance(vip, dict) or "id" not in vip or "name" not in vip:
                    continue
                save_vip(vip)
                sincronizados += 1
                # Processa associações VIP↔evento com task_id embutidas no objeto VIP
                for assoc in vip.get("event_vips", []) or []:
                    event_id = assoc.get("event_id")
                    task_id  = assoc.get("task_id")
                    if event_id:
                        try:
                            assign_vip_to_event(event_id, vip["id"], task_id)
                        except Exception as e:
                            print(f"Erro ao associar VIP {vip['id']} ao evento {event_id}: {e}")
            except Exception as e:
                print(f"Erro ao sincronizar VIP {vip.get('id')}: {e}")
                erros += 1
    except Exception as e:
        print(f"Erro ao ler servidor central (vips): {e}")
        return {"sincronizados": 0, "erros": 1, "msg": str(e)}

    return {"sincronizados": sincronizados, "erros": erros}


def sync_clientes_from_server() -> dict:
    """Baixa o catálogo de clientes/regionais do servidor central e grava o cache local
    (data/clientes.json) usado por core.credentials para resolver IPs e popular a UI.
    Mantém o app funcional offline depois do 1º sync."""
    settings = get_settings()
    server_url = settings.get("server_url", "").strip()
    if not server_url:
        return {"ok": False, "msg": "Servidor não configurado."}
    requests = _get_requests()
    try:
        resp = requests.get(f"{server_url.rstrip('/')}/api/clientes", timeout=5)
        if resp.status_code != 200:
            return {"ok": False, "msg": f"HTTP {resp.status_code}"}
        clientes = resp.json()
        if not isinstance(clientes, list):
            return {"ok": False, "msg": "Formato inválido."}
        catalogo = {}
        for c in clientes:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            regionais = {}
            for r in c.get("regionais", []) or []:
                region = (r.get("region") or "").strip().upper()
                ip = (r.get("ip") or "").strip()
                if region and ip:
                    regionais[region] = ip
            catalogo[c["name"]] = {
                "name": c["name"],
                "logo": c.get("logo", "") or "",
                "regionais": regionais,
            }
        from core import credentials as _cred
        f = _cred.clientes_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(catalogo, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "clientes": len(catalogo)}
    except Exception as e:
        print(f"Erro ao sincronizar clientes do servidor: {e}")
        return {"ok": False, "msg": str(e)}


def export_vip_to_server(vip: dict) -> bool:
    """Exporta um VIP global para o servidor central."""
    settings = get_settings()
    server_url = settings.get("server_url", "").strip()
    if not server_url:
        return False
    requests = _get_requests()
    url = f"{server_url.rstrip('/')}/api/vips"
    try:
        return requests.post(url, json=vip, timeout=5).status_code == 200
    except Exception as e:
        print(f"Erro ao exportar VIP para o servidor: {e}")
        return False


def delete_vip_on_server(vip_id: str) -> bool:
    """Remove o VIP do servidor central."""
    settings = get_settings()
    server_url = settings.get("server_url", "").strip()
    if not server_url:
        return False
    requests = _get_requests()
    url = f"{server_url.rstrip('/')}/api/vips/{vip_id}"
    try:
        return requests.delete(url, timeout=5).status_code == 200
    except Exception as e:
        print(f"Erro ao remover VIP no servidor: {e}")
        return False


def migrate_event_vip_measurements(event_id: str) -> int:
    """
    Copia os registros de vip_measurements do banco específico do evento (legado)
    para o banco global, ignorando duplicatas (UNIQUE vip_name + timestamp).

    Roda apenas uma vez por evento por sessão Python. Retorna o número de
    registros efetivamente inseridos no banco global.
    """
    if event_id in _migrated_events:
        return 0
    _migrated_events.add(event_id)

    try:
        e_conn = get_event_conn(event_id)
        # A tabela pode não existir em eventos criados após a refatoração
        try:
            rows = e_conn.execute(
                "SELECT vip_name, event_id, timestamp, serving_cell, rsrp, rsrq, in_event "
                "FROM vip_measurements WHERE event_id = ?",
                (event_id,)
            ).fetchall()
        except sqlite3.OperationalError:
            return 0  # tabela não existe neste banco de evento

        if not rows:
            return 0

        g_conn = get_conn()
        count_before = g_conn.execute(
            "SELECT COUNT(*) FROM vip_measurements WHERE event_id = ?", (event_id,)
        ).fetchone()[0]

        g_conn.executemany("""
            INSERT OR IGNORE INTO vip_measurements
                (vip_name, event_id, timestamp, serving_cell, rsrp, rsrq, in_event)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (r["vip_name"], r["event_id"], r["timestamp"],
             r["serving_cell"], r["rsrp"], r["rsrq"], r["in_event"])
            for r in rows
        ])
        g_conn.commit()

        count_after = g_conn.execute(
            "SELECT COUNT(*) FROM vip_measurements WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        migrated = count_after - count_before

        if migrated > 0:
            _db_logger.info(
                f"Migração VIPs evento '{event_id}': {migrated} de {len(rows)} "
                f"registros copiados para o banco global."
            )
        return migrated

    except Exception as e:
        _db_logger.warning(f"Erro ao migrar VIPs do evento '{event_id}': {e}")
        return 0


def clear_event_history(event_id: str):
    """
    Exclui medições de KPI e alertas do evento.
    VIP measurements NÃO são apagadas — são globais e compartilhadas entre eventos.
    """
    conn = get_event_conn(event_id)
    conn.execute("DELETE FROM kpi_measurements WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM alerts WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM alarms WHERE event_id = ?", (event_id,))
    conn.commit()

    # Executa VACUUM fora de qualquer transação para liberar espaço em disco
    old_isolation = conn.isolation_level
    try:
        conn.isolation_level = None  # Ativa modo autocommit temporariamente
        conn.execute("VACUUM")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Não foi possível rodar VACUUM no banco: {e}")
    finally:
        conn.isolation_level = old_isolation


