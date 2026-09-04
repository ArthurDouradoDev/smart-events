import csv
import hashlib
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from core import database as db
from core.event_export import EventExportService, _time_fields


def _wait(service, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = service.status(job_id)
        if job["status"] in {"ready", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("export job did not finish")


def _seed(event_in_db):
    event_id = event_in_db["id"]
    db.insert_kpi_batch([
        {"event_id": event_id, "site_id": "SR-SPPNB2", "cell_id": "SR-SPPNB2_1",
         "timestamp": "2026-08-21T02:59:59Z", "metric": "utilization_dl", "value": 10,
         "scope": "CELL", "technology": "4G"},
        {"event_id": event_id, "site_id": "SR-SPPNB2", "cell_id": "SR-SPPNB2_1",
         "timestamp": "2026-08-21T03:00:00Z", "metric": "utilization_dl", "value": 20,
         "scope": "CELL", "technology": "5G_NRCELL"},
        {"event_id": event_id, "site_id": "SR-SPPNB2", "cell_id": "UNKNOWN-CELL",
         "timestamp": "2026-08-21T03:01:00Z", "metric": "custom_metric", "value": 30,
         "scope": "CELL", "technology": ""},
    ])
    db.insert_vip_batch([{
        "event_id": event_id, "vip_name": "Pessoa Teste", "task_id": 9, "serial_no": 1,
        "timestamp": "2026-08-21T03:00:00Z", "serving_cell": "SR-SPPNB2_1",
        "rsrp": -95, "rsrq": -10, "in_event": True,
    }])
    db.insert_alarms_batch([{
        "csn": 1, "event_id": event_id, "alarm_id": "A", "alarm_group_id": "G",
        "alarm_name": "=FORMULA", "severity": "Major", "source": "SITE",
        "ip": "10.0.0.1", "location": "local", "occur_time": "2026-08-21T02:59:00Z",
        "arrive_time": "2026-08-21T03:00:00Z", "additional_info": "info",
        "collected_at": "2026-08-21T03:00:01Z",
    }])
    db.insert_alert({
        "event_id": event_id, "level": "INSTANCE", "severity": "WARNING",
        "site_id": "SR-SPPNB2", "cell_id": "SR-SPPNB2_1", "message": "alerta",
        "timestamp": "2026-08-21T03:00:02Z",
    })
    alert_id = db.get_all_alerts(event_id)[0]["id"]
    db.acknowledge_alert(event_id, alert_id)


def _export(tmp_path, event, options):
    service = EventExportService(tmp_path / "exports", tmp_path / "out")
    started = service.start(event["id"], options)
    job = _wait(service, started["job_id"])
    assert job["status"] == "ready", job.get("error")
    return zipfile.ZipFile(job["result"]["path"]), job


def test_brasilia_day_boundary_is_independent_of_machine_timezone():
    assert _time_fields("2026-08-21T02:59:59Z")["date"] == "2026-08-20"
    assert _time_fields("2026-08-21T03:00:00Z")["date"] == "2026-08-21"
    assert _time_fields("2026-08-21T03:00:00")["brasilia"].endswith("-03:00")


@pytest.mark.parametrize("options", [
    {"time_partition": "weekly"},
    {"technology_partition": "vendor"},
    {"include_vips": "yes"},
])
def test_invalid_options_are_rejected_before_job_creation(
    tmp_db, sample_event, tmp_path, options
):
    db.save_event(sample_event)
    service = EventExportService(tmp_path / "exports", tmp_path / "out")

    with pytest.raises(ValueError):
        service.start(sample_event["id"], options)

    assert list((tmp_path / "exports" / "jobs").iterdir()) == []


def test_preview_period_excludes_vips_when_they_are_not_selected(
    event_in_db, sample_event, tmp_path
):
    db.insert_vip_batch([{
        "event_id": sample_event["id"], "vip_name": "Pessoa", "task_id": 1,
        "serial_no": 1, "timestamp": "2026-08-21T03:00:00Z",
        "serving_cell": "C", "rsrp": -95, "rsrq": -10, "in_event": True,
    }])
    service = EventExportService(tmp_path / "exports", tmp_path / "out")

    without_vips = service.preview(sample_event["id"], {"include_vips": False})
    with_vips = service.preview(sample_event["id"], {"include_vips": True})

    assert without_vips["counts"]["vips"] == 0
    assert without_vips["period"]["covered_days"] == 0
    assert with_vips["counts"]["vips"] == 1
    assert with_vips["period"]["covered_days"] == 1


def test_complete_export_has_manifest_all_datasets_and_no_secrets(tmp_db, sample_event, tmp_path):
    event = dict(sample_event)
    event["oss"] = {**sample_event["oss"], "base_url": "https://secret.internal", "import_folder": "C:/private"}
    event["sites"] = [{
        **sample_event["sites"][0],
        "ep": {"enodebid": "111", "nename": "SITE EP", "latitude": -23.58, "longitude": -46.69},
        "cells": [{
            **sample_event["sites"][0]["cells"][0],
            "tech": "4G", "frequency": "1800", "earfcn": "1276",
            "ep": {"source_row": 2, "cellid": "10", "cellname": "SR-SPPNB2_1",
                   "azimuth": 0, "technology": "4G", "band": "1800", "dlearfcn": "1276"},
        }],
    }]
    db.save_event(event)
    _seed(event)
    archive, job = _export(tmp_path, event, {"time_partition": "consolidated", "technology_partition": "combined", "include_vips": True})
    with archive:
        names = set(archive.namelist())
        assert {"manifest.json", "LEIA-ME.txt", "dados/kpis.csv", "dados/vips.csv",
                "dados/alarmes.csv", "dados/alertas.csv", "metadados/ep_normalizada.csv"} <= names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["timezone"] == "America/Sao_Paulo"
        assert manifest["counts"] == {"kpis": 3, "vips": 1, "alarms": 1, "alerts": 1}
        for name, info in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == info["sha256"]
        event_json = archive.read("metadados/evento.json").decode("utf-8")
        assert "secret.internal" not in event_json
        assert "C:/private" not in event_json
        kpis = list(csv.DictReader(io.StringIO(archive.read("dados/kpis.csv").decode("utf-8-sig"))))
        assert len(kpis) == 3
        assert kpis[0]["site_name_ep"] == "SITE EP"
        assert kpis[0]["frequency_mhz"] == "1800"
        alarms = archive.read("dados/alarmes.csv").decode("utf-8-sig")
        assert "'=FORMULA" in alarms
    assert job["result"]["records"] == 6


def test_daily_and_family_partition_never_drops_unknown(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    _seed(sample_event)
    archive, _ = _export(tmp_path, sample_event, {
        "time_partition": "daily", "technology_partition": "family", "include_vips": False,
    })
    with archive:
        kpi_names = sorted(name for name in archive.namelist() if name.startswith("dados/kpis/"))
        assert kpi_names == [
            "dados/kpis/2026-08-20/kpis_4g.csv",
            "dados/kpis/2026-08-21/kpis_5g.csv",
            "dados/kpis/2026-08-21/kpis_unknown.csv",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["counts"]["vips"] == 0
        assert sum(manifest["files"][name]["rows"] for name in kpi_names) == 3


def test_export_filters_vips_by_event(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    other = {**sample_event, "id": "other-event", "name": "Outro"}
    db.save_event(other)
    _seed(sample_event)
    db.insert_vip_batch([{
        "event_id": other["id"], "vip_name": "Pessoa Teste", "task_id": 99, "serial_no": 2,
        "timestamp": "2026-08-21T03:00:00Z", "serving_cell": "OTHER",
        "rsrp": -100, "rsrq": -12, "in_event": False,
    }])
    archive, _ = _export(tmp_path, sample_event, {})
    with archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("dados/vips.csv").decode("utf-8-sig"))))
        assert len(rows) == 1
        assert rows[0]["event_id"] == sample_event["id"]


def test_empty_export_writes_headers(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    archive, _ = _export(tmp_path, sample_event, {})
    with archive:
        for name in ("dados/kpis.csv", "dados/vips.csv", "dados/alarmes.csv", "dados/alertas.csv"):
            content = archive.read(name).decode("utf-8-sig").splitlines()
            assert len(content) == 1


def test_collector_partition_separates_5g_types(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    db.insert_kpi_batch([
        {"event_id": sample_event["id"], "site_id": "S", "cell_id": "C1",
         "timestamp": "2026-08-21T03:00:00Z", "metric": "users", "value": 1,
         "scope": "CELL", "technology": "5G_NRCELL"},
        {"event_id": sample_event["id"], "site_id": "S", "cell_id": "C2",
         "timestamp": "2026-08-21T03:00:00Z", "metric": "users", "value": 2,
         "scope": "CELL", "technology": "5G_NRDUCELL"},
    ])

    archive, _ = _export(tmp_path, sample_event, {
        "technology_partition": "collector", "time_partition": "consolidated",
    })
    with archive:
        assert {
            "dados/kpis/kpis_5g_nrcell.csv",
            "dados/kpis/kpis_5g_nrducell.csv",
        } <= set(archive.namelist())


def test_invalid_timestamp_is_kept_and_reported(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    db.insert_kpi_batch([{
        "event_id": sample_event["id"], "site_id": "S", "cell_id": "C",
        "timestamp": "timestamp-invalido", "metric": "users", "value": 1,
        "scope": "CELL", "technology": "4G",
    }])

    archive, _ = _export(tmp_path, sample_event, {
        "time_partition": "daily", "technology_partition": "combined",
    })
    with archive:
        assert "dados/kpis/kpis_data-invalida.csv" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert {warning["code"] for warning in manifest["warnings"]} >= {
            "invalid_kpi_timestamps"
        }


def test_export_does_not_apply_alarm_ui_limit(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    db.insert_alarms_batch([
        {
            "csn": index, "event_id": sample_event["id"], "alarm_id": f"A-{index}",
            "alarm_group_id": "G", "alarm_name": "Alarm", "severity": "Major",
            "source": "SITE", "ip": "", "location": "", "additional_info": "",
            "occur_time": "2026-08-21T03:00:00Z",
            "arrive_time": "2026-08-21T03:00:00Z",
            "collected_at": "2026-08-21T03:00:01Z",
        }
        for index in range(1, 502)
    ])

    archive, _ = _export(tmp_path, sample_event, {})
    with archive:
        rows = list(csv.DictReader(io.StringIO(
            archive.read("dados/alarmes.csv").decode("utf-8-sig")
        )))
        assert len(rows) == 501


def test_cancelled_export_removes_staging_and_publishes_nothing(
    tmp_db, sample_event, tmp_path, monkeypatch
):
    db.save_event(sample_event)
    service = EventExportService(tmp_path / "exports", tmp_path / "out")
    entered = threading.Event()

    def blocked_backup(job_id, _source, _destination):
        entered.set()
        while True:
            service._check_cancel(job_id)
            time.sleep(0.01)

    monkeypatch.setattr(service, "_backup", blocked_backup)
    started = service.start(sample_event["id"], {})
    assert entered.wait(timeout=2)
    service.cancel(started["job_id"])
    job = _wait(service, started["job_id"])

    assert job["status"] == "cancelled"
    assert list((tmp_path / "out").glob("*.zip")) == []
    assert list((tmp_path / "exports" / ".staging").iterdir()) == []
    assert service.cancel(started["job_id"])["status"] == "cancelled"


def test_failed_export_has_sanitized_error_and_no_partial_zip(
    tmp_db, sample_event, tmp_path, monkeypatch
):
    db.save_event(sample_event)
    service = EventExportService(tmp_path / "exports", tmp_path / "out")

    def fail_validation(*_args):
        raise RuntimeError(f"secret-path={tmp_path}")

    monkeypatch.setattr(service, "_validate_zip", fail_validation)
    started = service.start(sample_event["id"], {})
    job = _wait(service, started["job_id"])

    assert job["status"] == "failed"
    assert str(tmp_path) not in job["error"]
    assert list((tmp_path / "out").glob("*.zip")) == []
    assert list((tmp_path / "exports" / ".staging").iterdir()) == []


def test_existing_destination_gets_unique_suffix(tmp_path):
    event = {"id": "evento-1"}
    first = EventExportService._unique_destination(tmp_path, event)
    first.write_bytes(b"nao-sobrescrever")
    second = EventExportService._unique_destination(tmp_path, event)

    assert second != first
    assert second.stem.endswith("-2")
    assert first.read_bytes() == b"nao-sobrescrever"


def test_latest_export_ignores_removed_artifact(tmp_db, sample_event, tmp_path):
    db.save_event(sample_event)
    service = EventExportService(tmp_path / "exports", tmp_path / "out")
    started = service.start(sample_event["id"], {})
    job = _wait(service, started["job_id"])
    assert service.latest_for_event(sample_event["id"])["job_id"] == job["job_id"]

    path = Path(job["result"]["path"])
    path.unlink()
    assert service.latest_for_event(sample_event["id"]) is None
