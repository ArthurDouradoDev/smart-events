from core.collector import HttpCollector
from core import database as db


def _event():
    return {
        "id": "monitoring-phase2", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [{"task_id": 10, "tech": "4G"}, {"task_id": 20, "tech": "5G"}]},
        "sites": [{"id": "SITE", "name": "SITE", "cells": [
            {"id": "4G-CELL", "tech": "4G", "obj_no": 1},
            {"id": "5G-CELL", "tech": "5G", "obj_no": 2},
        ]}],
    }


def _item(obj_no, name, counters):
    return {"obj": {"objNo": obj_no, "objName": f"Cell Name={name}"},
            "counterRes": [{"name": key, "value": value, "reliable": 1} for key, value in counters.items()]}


def test_counter_res_is_calculated_and_site_rows_are_persisted(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event(), "https://oss.example")
    response = {"data": [
        {"taskId": 10, "execTime": 1_700_000_000_000, "objNoExecTimes": [{"objNo": 1, "preExecTime": 1_700_000_000_000}],
         "results": [{"execTime": 1_700_000_000_000, "period": 5, "objRes": [_item(1, "4G-CELL", {
            "L.RRC.ConnReq.Succ": 10, "L.RRC.ConnReq.Att": 10, "L.E-RAB.SuccEst": 10, "L.E-RAB.AttEst": 10,
            "L.S1Sig.ConnEst.Succ": 10, "L.S1Sig.ConnEst.Att": 10, "L.E-RAB.AbnormRel": 0,
            "L.E-RAB.AbnormRel.MME": 0, "L.E-RAB.NormRel": 10, "L.E-RAB.Rel.MME": 0,
            "L.Cell.Unavail.Dur.Sys": 0, "L.Cell.Unavail.Dur.Manual": 0,
            "L.ChMeas.PRB.DL.Used.Avg": 20, "L.ChMeas.PRB.DL.Avail": 100,
        })]}]},
    ]}
    parsed = collector._parse_monitoring_response(response, {"10": "4G"})
    assert any(row["metric"] == "accessibility" and row["value"] == 100 for row in parsed["rows"])
    assert any(row["scope"] == "SITE" and row["metric"] == "accessibility" for row in parsed["rows"])
    first = db.insert_kpi_batch(parsed["rows"])
    replay = db.insert_kpi_batch(parsed["rows"])
    assert first["inserted"] == len(parsed["rows"])
    assert replay["inserted"] == 0
    assert replay["duplicate"] == len(parsed["rows"])


def test_unreliable_and_non_numeric_counters_create_diagnostics(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event(), "https://oss.example")
    response = {"data": [{"taskId": 10, "results": [{"period": 5, "objRes": [_item(1, "4G-CELL", {
        "L.RRC.ConnReq.Succ": "not-number", "L.RRC.ConnReq.Att": 0,
    })]}]}]}
    response["data"][0]["results"][0]["objRes"][0]["counterRes"][1]["reliable"] = 0
    parsed = collector._parse_monitoring_response(response, {"10": "4G"})
    assert parsed["invalid"] > 0
    assert any(item.code == "invalid_counter" for item in parsed["diagnostics"])
