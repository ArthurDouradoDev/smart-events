"""
Testes unitários para a coleta de alarmes (sem VPN).

Cobre: parse do catálogo, resolução de nomes → pares, montagem da condition,
achatamento, dedup por csn no banco, ordenação/timestamp de get_alarms,
MockCollector.collect_alarms e os métodos de alarme da Api.
Integração real (HttpCollector.collect_alarms) fica em test_http_vpn.py.
"""
import pytest

import core.database as db
from core.collector import (
    MockCollector,
    HttpCollector,
    _load_alarm_catalog,
    _resolve_alarm_pairs,
    _build_alarm_condition,
    _extract_alarm_model_id,
    _flatten_alarm,
    _DEFAULT_ALARM_NAMES,
)


# ── Catálogo / resolução / condition ──────────────────────────────────

class TestAlarmCatalog:
    def test_catalog_loads_defaults(self):
        catalog = _load_alarm_catalog()
        assert isinstance(catalog, dict)
        # Os dois tipos padrão devem existir no catálogo exportado.
        for name in _DEFAULT_ALARM_NAMES:
            assert name in catalog, f"{name!r} ausente no catálogo"
            assert catalog[name], f"{name!r} sem pares (alarmId, alarmGroupId)"

    def test_catalog_pairs_are_tuples(self):
        catalog = _load_alarm_catalog()
        pairs = catalog[_DEFAULT_ALARM_NAMES[0]]
        for aid, gid in pairs:
            assert aid and gid

    def test_resolve_defaults(self):
        catalog = _load_alarm_catalog()
        pairs, missing = _resolve_alarm_pairs(_DEFAULT_ALARM_NAMES, catalog)
        assert missing == []
        assert len(pairs) >= 1

    def test_resolve_lenient_on_missing(self):
        catalog = _load_alarm_catalog()
        pairs, missing = _resolve_alarm_pairs(
            [_DEFAULT_ALARM_NAMES[0], "Nome Inexistente XYZ"], catalog)
        assert "Nome Inexistente XYZ" in missing
        assert len(pairs) >= 1  # o nome válido ainda resolve

    def test_resolve_dedup(self):
        catalog = _load_alarm_catalog()
        name = _DEFAULT_ALARM_NAMES[0]
        pairs, _ = _resolve_alarm_pairs([name, name], catalog)
        assert len(pairs) == len(set(pairs))


class TestAlarmCondition:
    def test_build_condition_with_pairs(self):
        import json
        cond = json.loads(_build_alarm_condition([("6486", "8193")]))
        assert cond["alarmGroupId"]["operation"] == "in"
        assert cond["alarmGroupId"]["value"] == [{"alarmId": "6486", "alarmGroupId": "8193"}]
        # Chaves fixas que a tela "Current Alarms" sempre envia (paridade com o HAR).
        assert cond["alarmLevel"] == ["CRITICAL", "MAJOR", "MINOR", "WARNING"]
        assert cond["alarmStatus"] == [12, 10, 11, 13]

    def test_build_condition_empty_pairs(self):
        import json
        cond = json.loads(_build_alarm_condition([]))
        assert "alarmGroupId" not in cond  # sem restrição de grupo

    def test_extract_model_id_variants(self):
        assert _extract_alarm_model_id({"parameters": {"modelID": "m1"}}) == "m1"
        assert _extract_alarm_model_id({"modelId": "m2"}) == "m2"
        assert _extract_alarm_model_id({"parameters": {"result": {"modelID": "m3"}}}) == "m3"
        assert _extract_alarm_model_id({"parameters": {}}) is None


class TestFlattenAlarm:
    def test_flatten_maps_fields(self):
        raw = {
            "csn": 123, "alarmId": 6486, "alarmGroupId": 8193,
            "alarmName": "Cell Unavailable", "severity": 2,
            "meName": "SR-UWCTJ1", "address": "10.0.0.1", "subNet": "SP",
            "arriveUtc": "2026-06-30T12:00:00Z", "occurUtc": "2026-06-30T11:59:00Z",
            "additionalInformation": "info",
        }
        row = _flatten_alarm(raw, "evt-1", "2026-06-30T12:01:00Z")
        assert row["csn"] == 123
        assert row["event_id"] == "evt-1"
        assert row["alarm_name"] == "Cell Unavailable"
        assert row["severity"] == "Major"  # 2 → Major
        assert row["source"] == "SR-UWCTJ1"
        assert row["collected_at"] == "2026-06-30T12:01:00Z"


class TestAlarmTimezone:
    """arriveUtc/occurUtc vêm no fuso do cliente (UTC-3) apesar do nome 'Utc'.
    _flatten_alarms deve convertê-los para UTC real (soma +3h com offset -180)."""

    def test_flatten_alarms_converts_local_to_utc(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = HttpCollector(sample_event, "")  # offset default -180 (America/Sao_Paulo)
        raw = [{
            "csn": 1, "alarmId": "26529", "alarmGroupId": "8193",
            "alarmName": "RF Unit VSWR Threshold Crossed", "severity": 3,
            "meName": "SR-UWCTJ1",
            "arriveUtc": "2026-06-24 15:45:03", "occurUtc": "2026-06-24 15:45:00",
        }]
        rows = c._flatten_alarms(raw)
        assert rows[0]["arrive_time"] == "2026-06-24T18:45:03Z"  # 15:45 local → 18:45 UTC
        assert rows[0]["occur_time"] == "2026-06-24T18:45:00Z"


# ── MockCollector.collect_alarms ──────────────────────────────────────

class TestMockCollectAlarms:
    def test_returns_flattened_rows(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rows = c.collect_alarms().measurements
        assert len(rows) > 0
        first = rows[0]
        assert isinstance(first, dict)
        for key in ("csn", "event_id", "alarm_name", "severity", "collected_at"):
            assert key in first
        assert first["event_id"] == sample_event["id"]

    def test_unique_csn(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rows = c.collect_alarms().measurements
        csns = [r["csn"] for r in rows]
        assert len(csns) == len(set(csns))


# ── Banco: insert dedup + get_alarms ──────────────────────────────────

class TestAlarmDatabase:
    def _row(self, event_id, csn, arrive, name="Cell Unavailable", collected="2026-06-30T12:00:00Z"):
        return {
            "csn": csn, "event_id": event_id, "alarm_id": "6486",
            "alarm_group_id": "8193", "alarm_name": name, "severity": "Major",
            "source": "SR-X", "ip": "10.0.0.1", "location": "SP",
            "occur_time": arrive, "arrive_time": arrive, "additional_info": "",
            "collected_at": collected,
        }

    def test_insert_dedup_by_csn(self, event_in_db, sample_event):
        eid = sample_event["id"]
        row = self._row(eid, 555, "2026-06-30T12:00:00Z")
        db.insert_alarms_batch([row, row])  # csn repetido
        alarms = db.get_alarms(eid)
        assert len([a for a in alarms if a["csn"] == 555]) == 1

    def test_get_alarms_order_desc(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1, "2026-06-30T10:00:00Z"),
            self._row(eid, 2, "2026-06-30T12:00:00Z"),
            self._row(eid, 3, "2026-06-30T11:00:00Z"),
        ])
        alarms = db.get_alarms(eid)
        arrive = [a["arrive_time"] for a in alarms]
        assert arrive == sorted(arrive, reverse=True)

    def test_get_alarms_timestamp_filter(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1, "2026-06-30T10:00:00Z", collected="2026-06-30T10:00:00Z"),
            self._row(eid, 2, "2026-06-30T11:00:00Z", collected="2026-06-30T13:00:00Z"),
        ])
        alarms = db.get_alarms(eid, timestamp="2026-06-30T12:00:00Z")
        csns = {a["csn"] for a in alarms}
        assert csns == {1}  # csn 2 foi coletado depois do corte

    def test_clear_event_history_removes_alarms(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 9, "2026-06-30T10:00:00Z")])
        db.clear_event_history(eid)
        assert db.get_alarms(eid) == []


# ── Api ───────────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_db, monkeypatch):
    from core import scheduler as sched_module
    from api.api import Api
    monkeypatch.setattr(sched_module.scheduler, "_recording", False)
    return Api()


class TestApiAlarms:
    def test_get_alarm_catalog(self, api):
        res = api.get_alarm_catalog()
        assert res["ok"] is True
        assert isinstance(res["names"], list)
        assert _DEFAULT_ALARM_NAMES[0] in res["names"]

    def test_get_alarm_filter_default(self, api, sample_event):
        db.save_event(sample_event)
        res = api.get_alarm_filter(sample_event["id"])
        assert res["ok"] is True
        assert res["names"]  # default aplicado quando não definido

    def test_set_and_get_alarm_filter(self, api, sample_event):
        db.save_event(sample_event)
        names = ["Cell Unavailable"]
        res = api.set_alarm_filter(sample_event["id"], names)
        assert res["ok"] is True
        got = api.get_alarm_filter(sample_event["id"])
        assert got["names"] == names

    def test_set_alarm_filter_rejects_non_list(self, api, sample_event):
        db.save_event(sample_event)
        res = api.set_alarm_filter(sample_event["id"], "nao-e-lista")
        assert res["ok"] is False

    def test_get_alarms_returns_list(self, api, sample_event):
        db.save_event(sample_event)
        assert isinstance(api.get_alarms(sample_event["id"]), list)

    def test_get_alarms_marks_in_event(self, api, sample_event):
        db.save_event(sample_event)
        eid = sample_event["id"]
        # sample_event tem o site SR-SPPNB2; source igual ao meName deve casar.
        db.insert_alarms_batch([
            {"csn": 1, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": "Cell Unavailable", "severity": "Major", "source": "SR-SPPNB2",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
            {"csn": 2, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": "Cell Unavailable", "severity": "Major", "source": "SR-OUTRO99",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
        ])
        by_csn = {a["csn"]: a for a in api.get_alarms(eid)}
        assert by_csn[1]["in_event"] is True
        assert by_csn[1]["serving_site"] == "SR-SPPNB2"
        assert by_csn[2]["in_event"] is False

    def test_resolve_site_for_source(self, api, sample_event):
        sites = sample_event["sites"]
        assert api._resolve_site_for_source(sites, "SR-SPPNB2")[0] == "SR-SPPNB2"
        assert api._resolve_site_for_source(sites, "SR-SPPNB2-3")[0] == "SR-SPPNB2"  # prefixo
        assert api._resolve_site_for_source(sites, "SR-NOPE")[0] is None
        assert api._resolve_site_for_source(sites, None)[0] is None

    def test_refresh_alarms_when_not_recording(self, api, sample_event):
        db.save_event(sample_event)
        res = api.refresh_alarms(sample_event["id"])
        assert res["ok"] is False  # coleta inativa
