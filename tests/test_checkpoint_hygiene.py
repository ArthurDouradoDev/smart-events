import json

import pytest

from core import database as database
from core.checkpoint_hygiene import apply_report, audit_workspace, write_report


def _checkpoint(conn, event_id, oss, collector, task_id, object_key="", cursor="1"):
    conn.execute("""
        INSERT INTO collection_checkpoints
            (event_id, oss, collector, task_id, object_key, cursor, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, '2026-08-14T12:00:00Z')
    """, (event_id, oss, collector, str(task_id), object_key, cursor))
    conn.commit()


def test_auditoria_e_aplicacao_exigem_hash_e_criam_backup(tmp_db, sample_event, tmp_path):
    event = {
        **sample_event,
        "integration": {"pm_task_id": 747},
        "vips": [],
    }
    database.save_event(event)
    database.save_vip({
        "id": "vip-sp", "name": "VIP SP", "oss": "SP", "cliente": "TIM", "task_id": 2072,
    })
    conn = database.get_event_conn(event["id"])
    _checkpoint(conn, event["id"], "SP", "monitoring", 747)       # válido
    _checkpoint(conn, event["id"], "OUTRAS", "monitoring", 2225) # OSS e task incompatíveis
    _checkpoint(conn, event["id"], "SP", "vip", 2073, "row")     # VIP de outra regional

    report = audit_workspace(database.DB_PATH, database.get_event_db_path)
    invalid = report["invalid_checkpoints"]
    assert len(invalid) == 2
    assert {item["task_id"] for item in invalid} == {"2225", "2073"}
    assert any("oss_incompativel" in item["reasons"] for item in invalid)
    assert any("task_vip_nao_pertence_ao_evento_oss" in item["reasons"] for item in invalid)

    report_path = write_report(report, tmp_path / "diagnostics")
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="Hash de confirmação"):
        apply_report(persisted, "hash-incorreto", tmp_path / "backups")

    result = apply_report(persisted, persisted["confirmation_hash"], tmp_path / "backups")
    assert result["removed"] == 2
    assert len(result["backups"]) == 1
    assert all(__import__("pathlib").Path(path).exists() for path in result["backups"].values())
    remaining = conn.execute(
        "SELECT oss, collector, task_id FROM collection_checkpoints"
    ).fetchall()
    assert [tuple(row) for row in remaining] == [("SP", "monitoring", "747")]


def test_saneamento_recusa_evento_ativo(tmp_db, sample_event, tmp_path):
    event = {**sample_event, "integration": {"pm_task_id": 747}}
    database.save_event(event)
    conn = database.get_event_conn(event["id"])
    _checkpoint(conn, event["id"], "OUTRAS", "monitoring", 2225)
    report = audit_workspace(database.DB_PATH, database.get_event_db_path)
    database.update_event_status(event["id"], "ACTIVE")

    with pytest.raises(RuntimeError, match="Encerre o evento ativo"):
        apply_report(report, report["confirmation_hash"], tmp_path / "backups")
