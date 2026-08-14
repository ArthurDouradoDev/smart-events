"""Auditoria e saneamento seguro de checkpoints multi-regionais.

O caminho padrão é estritamente somente leitura. A remoção exige um relatório
prévio, confirmação do hash, aplicação com a coleta parada e backup SQLite de
todos os bancos afetados.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


REPORT_SCHEMA = 1
CHECKPOINT_KEY = ("event_id", "oss", "collector", "task_id", "object_key")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _task_text(value) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def monitoring_tasks(config: dict) -> set[str]:
    """Extrai as tasks PM explicitamente pertencentes ao evento."""
    integration = config.get("integration") or {}
    tasks = integration.get("pm_tasks") or []
    if isinstance(tasks, dict):
        tasks = [{"task_id": value} for value in tasks.values()]
    found = {
        task
        for item in tasks
        if isinstance(item, dict)
        for task in [_task_text(item.get("task_id"))]
        if task
    }
    fallback = _task_text(integration.get("pm_task_id"))
    if fallback:
        found.add(fallback)
    return found


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _vip_tasks(global_conn: sqlite3.Connection, event_conn: sqlite3.Connection,
               config: dict) -> set[str]:
    oss = config.get("oss") if isinstance(config.get("oss"), dict) else {}
    region = (oss.get("region") or "").strip().upper()
    cliente = (oss.get("cliente") or "").strip()
    found = {
        task
        for item in (config.get("vips") or [])
        if isinstance(item, dict)
        for task in [_task_text(item.get("task_id"))]
        if task
    }
    if _table_exists(event_conn, "event_vips"):
        found.update(
            str(row[0]) for row in event_conn.execute(
                "SELECT DISTINCT task_id FROM event_vips WHERE event_id=? AND task_id IS NOT NULL",
                (config.get("id"),),
            )
        )
    if region and cliente and _table_exists(global_conn, "vips"):
        found.update(
            str(row[0]) for row in global_conn.execute("""
                SELECT DISTINCT task_id FROM vips
                WHERE UPPER(COALESCE(oss, '')) = ?
                  AND (cliente = ? OR cliente IS NULL OR cliente = '')
                  AND task_id IS NOT NULL
            """, (region, cliente))
        )
    return found


def _candidate_signature(candidates: list[dict]) -> str:
    exact = [
        {key: item[key] for key in (*CHECKPOINT_KEY, "cursor", "updated_at", "database")}
        for item in candidates
    ]
    encoded = json.dumps(exact, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def audit_workspace(global_db: Path, event_db_resolver) -> dict:
    """Gera um relatório exato, sem alterar nenhum banco.

    ``event_db_resolver`` recebe o id do evento e devolve seu caminho. A task PM
    só é julgada por pertencimento quando está explicitamente no config do evento;
    regional, identidade do evento e tasks VIP são sempre verificadas.
    """
    global_db = Path(global_db).resolve()
    candidates: list[dict] = []
    review: list[dict] = []
    scanned: list[dict] = []
    with sqlite3.connect(global_db) as global_conn:
        global_conn.row_factory = sqlite3.Row
        events = global_conn.execute("SELECT id, config_json FROM events ORDER BY id").fetchall()
        for event_row in events:
            try:
                config = json.loads(event_row["config_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                review.append({"event_id": event_row["id"], "reason": "config_json_invalido"})
                continue
            config["id"] = event_row["id"]
            db_path = Path(event_db_resolver(event_row["id"])).resolve()
            if not db_path.exists():
                review.append({"event_id": event_row["id"], "reason": "banco_evento_ausente",
                               "database": str(db_path)})
                continue
            with sqlite3.connect(db_path) as event_conn:
                event_conn.row_factory = sqlite3.Row
                if not _table_exists(event_conn, "collection_checkpoints"):
                    scanned.append({"event_id": event_row["id"], "database": str(db_path), "rows": 0})
                    continue
                region = ((config.get("oss") or {}).get("region") or "").strip().upper()
                allowed_pm = monitoring_tasks(config)
                allowed_vip = _vip_tasks(global_conn, event_conn, config)
                rows = event_conn.execute("""
                    SELECT event_id, oss, collector, task_id, object_key, cursor, updated_at
                    FROM collection_checkpoints
                    ORDER BY event_id, oss, collector, task_id, object_key
                """).fetchall()
                scanned.append({
                    "event_id": event_row["id"], "database": str(db_path), "rows": len(rows),
                    "region": region, "monitoring_tasks": sorted(allowed_pm),
                    "vip_tasks": sorted(allowed_vip),
                })
                for row in rows:
                    item = dict(row)
                    reasons = []
                    if item["event_id"] != event_row["id"]:
                        reasons.append("event_id_incompativel")
                    if not region:
                        review.append({**item, "database": str(db_path),
                                       "reason": "regional_evento_nao_resolvida"})
                        continue
                    if (item["oss"] or "").strip().upper() != region:
                        reasons.append("oss_incompativel")
                    collector = item["collector"]
                    task = _task_text(item["task_id"])
                    if collector == "vip" and task not in allowed_vip:
                        reasons.append("task_vip_nao_pertence_ao_evento_oss")
                    elif collector == "monitoring" and allowed_pm and task not in allowed_pm:
                        reasons.append("task_pm_nao_pertence_ao_evento")
                    elif collector not in {"vip", "monitoring"}:
                        reasons.append("coletor_desconhecido")
                    if reasons:
                        candidates.append({**item, "database": str(db_path), "reasons": reasons})

    report = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "global_database": str(global_db),
        "scanned": scanned,
        "invalid_checkpoints": candidates,
        "manual_review": review,
    }
    report["confirmation_hash"] = _candidate_signature(candidates)
    return report


def write_report(report: dict, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"checkpoint-audit-{_utc_stamp()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _backup_database(source: Path, backup_dir: Path, stamp: str) -> Path:
    source = Path(source).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source.stem}-{stamp}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Backup inválido: {target}")
    return target


def apply_report(report: dict, confirmation_hash: str, backup_dir: Path) -> dict:
    """Remove somente as linhas exatas do relatório após backup consistente."""
    if report.get("schema_version") != REPORT_SCHEMA:
        raise ValueError("Versão de relatório incompatível")
    candidates = report.get("invalid_checkpoints") or []
    expected_hash = _candidate_signature(candidates)
    if confirmation_hash != expected_hash or report.get("confirmation_hash") != expected_hash:
        raise ValueError("Hash de confirmação não corresponde ao relatório")

    global_db = Path(report["global_database"])
    with sqlite3.connect(global_db) as conn:
        active = conn.execute("SELECT COUNT(*) FROM events WHERE status='ACTIVE'").fetchone()[0]
    if active:
        raise RuntimeError("Encerre o evento ativo antes de aplicar o saneamento")

    by_database: dict[Path, list[dict]] = {}
    for item in candidates:
        by_database.setdefault(Path(item["database"]).resolve(), []).append(item)
    stamp = _utc_stamp()
    backups = {
        str(path): str(_backup_database(path, Path(backup_dir), stamp))
        for path in sorted(by_database, key=str)
    }

    removed = 0
    for path, items in by_database.items():
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            for item in items:
                params = tuple(item[key] for key in CHECKPOINT_KEY) + (
                    item["cursor"], item["updated_at"],
                )
                found = conn.execute("""
                    SELECT 1 FROM collection_checkpoints
                    WHERE event_id=? AND oss=? AND collector=? AND task_id=? AND object_key=?
                      AND cursor=? AND updated_at=?
                """, params).fetchone()
                if not found:
                    raise RuntimeError(
                        "Checkpoint mudou desde a auditoria; gere um novo relatório antes de aplicar"
                    )
            for item in items:
                params = tuple(item[key] for key in CHECKPOINT_KEY) + (
                    item["cursor"], item["updated_at"],
                )
                cursor = conn.execute("""
                    DELETE FROM collection_checkpoints
                    WHERE event_id=? AND oss=? AND collector=? AND task_id=? AND object_key=?
                      AND cursor=? AND updated_at=?
                """, params)
                removed += cursor.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {"removed": removed, "backups": backups, "confirmation_hash": expected_hash}
