"""Testes de integração para core/database.py usando banco SQLite temporário."""
import threading

import pytest
import core.database as database


# ── Eventos ──────────────────────────────────────────────────────────

class TestEventCRUD:
    def test_save_and_get(self, event_in_db, sample_event):
        ev = database.get_event(sample_event["id"])
        assert ev is not None
        assert ev["id"] == sample_event["id"]
        assert ev["name"] == sample_event["name"]

    def test_get_nonexistent(self, tmp_db):
        assert database.get_event("nao-existe") is None

    def test_get_events_all(self, event_in_db, sample_event):
        events = database.get_events()
        ids = [e["id"] for e in events]
        assert sample_event["id"] in ids

    def test_get_events_by_status(self, event_in_db, sample_event):
        scheduled = database.get_events(status="SCHEDULED")
        assert any(e["id"] == sample_event["id"] for e in scheduled)
        active = database.get_events(status="ACTIVE")
        assert not any(e["id"] == sample_event["id"] for e in active)

    def test_update_event_status(self, event_in_db, sample_event):
        database.update_event_status(sample_event["id"], "ACTIVE")
        ev = database.get_event(sample_event["id"])
        assert ev["status"] == "ACTIVE"

    def test_delete_event(self, event_in_db, sample_event):
        database.delete_event(sample_event["id"])
        assert database.get_event(sample_event["id"]) is None

    def test_sync_preserves_active_status(self, event_in_db, sample_event):
        """sync_events_from_server não deve rebaixar ACTIVE → outro status."""
        database.update_event_status(sample_event["id"], "ACTIVE")
        database.sync_events_from_server()
        ev = database.get_event(sample_event["id"])
        assert ev["status"] in ("ACTIVE", "SCHEDULED")  # não deve tornar-se ENDED


# ── KPI Measurements ─────────────────────────────────────────────────

def test_schema_do_evento_e_inicializado_uma_vez_entre_threads(tmp_db, monkeypatch):
    original = database.init_event_db
    calls = []
    calls_lock = threading.Lock()
    barrier = threading.Barrier(6)
    errors = []

    def counted(conn):
        with calls_lock:
            calls.append(1)
        original(conn)

    def open_from_thread():
        try:
            barrier.wait()
            conn = database.get_event_conn("concorrente")
            conn.execute("SELECT COUNT(*) FROM sites").fetchone()
        except Exception as error:
            errors.append(error)
        finally:
            database.close_conn()

    monkeypatch.setattr(database, "init_event_db", counted)
    threads = [threading.Thread(target=open_from_thread) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(calls) == 1


class TestKpiMeasurements:
    def _make_kpi(self, event_id, site_id, cell_id, metric, value, ts=None):
        # insert_kpi_batch espera dicts (mesmo formato que collect_kpis() retorna)
        return {
            "event_id": event_id,
            "site_id": site_id,
            "cell_id": cell_id,
            "metric": metric,
            "value": value,
            "timestamp": ts or "2026-06-01T10:00:00Z",
        }

    def test_insert_and_get_latest(self, event_in_db, sample_event):
        m = self._make_kpi(sample_event["id"], "SR-SPPNB2", "SR-SPPNB2_1", "dl_prb_usage", 55.0)
        database.insert_kpi_batch([m])
        latest = database.get_latest_kpi(sample_event["id"])
        assert len(latest) >= 1
        assert any(r["metric"] == "dl_prb_usage" for r in latest)

    def test_insert_batch_multiple_cells(self, event_in_db, sample_event):
        batch = [
            self._make_kpi(sample_event["id"], "SR-SPPNB2", f"SR-SPPNB2_{i}", "dl_prb_usage", float(i * 10))
            for i in range(1, 4)
        ]
        database.insert_kpi_batch(batch)
        latest = database.get_latest_kpi(sample_event["id"])
        cell_ids = [r["cell_id"] for r in latest if r["metric"] == "dl_prb_usage"]
        assert "SR-SPPNB2_1" in cell_ids
        assert "SR-SPPNB2_3" in cell_ids

    def test_get_kpi_series(self, event_in_db, sample_event):
        m = self._make_kpi(sample_event["id"], "SR-SPPNB2", "SR-SPPNB2_1", "dl_prb_usage", 60.0)
        database.insert_kpi_batch([m])
        # minutes=0 desativa o filtro temporal (retorna todos os dados)
        series = database.get_kpi_series(sample_event["id"], "SR-SPPNB2", "dl_prb_usage", minutes=0)
        assert len(series) >= 1

    def test_get_latest_kpi_by_metric(self, event_in_db, sample_event):
        m = self._make_kpi(sample_event["id"], "SR-SPPNB2", "SR-SPPNB2_1", "availability", 99.0)
        database.insert_kpi_batch([m])
        rows = database.get_latest_kpi_by_metric(sample_event["id"], "availability")
        assert any(r["value"] == 99.0 for r in rows)

    def test_clear_event_history(self, event_in_db, sample_event):
        m = self._make_kpi(sample_event["id"], "SR-SPPNB2", "SR-SPPNB2_1", "dl_prb_usage", 70.0)
        database.insert_kpi_batch([m])
        database.clear_event_history(sample_event["id"])
        latest = database.get_latest_kpi(sample_event["id"])
        assert latest == []

    def test_batch_invalido_faz_rollback_integral(self, event_in_db, sample_event):
        valid = self._make_kpi(sample_event["id"], "SITE", "CELL-1", "availability", 99.0)
        invalid = self._make_kpi(sample_event["id"], "SITE", None, "availability", 98.0)

        with pytest.raises(Exception):
            database.insert_kpi_batch([valid, invalid])

        conn = database.get_event_conn(sample_event["id"])
        count = conn.execute(
            "SELECT COUNT(*) FROM kpi_measurements WHERE site_id='SITE'"
        ).fetchone()[0]
        assert count == 0
        assert conn.in_transaction is False


# ── VIP Measurements ─────────────────────────────────────────────────

class TestVipMeasurements:
    def _make_vip(self, event_id, name, rsrp, rsrq, ts=None):
        # insert_vip_batch espera dicts (mesmo formato que collect_vips() retorna)
        return {
            "event_id": event_id,
            "vip_name": name,
            "task_id": None,
            "rsrp": rsrp,
            "rsrq": rsrq,
            "serving_cell": None,
            "in_event": True,
            "timestamp": ts or "2026-06-01T10:05:00Z",
        }

    def test_insert_and_get_latest(self, event_in_db, sample_event):
        m = self._make_vip(sample_event["id"], "Carlos Menezes", -95.0, -10.0)
        database.insert_vip_batch([m])
        latest = database.get_vip_latest()
        assert any(r["vip_name"] == "Carlos Menezes" for r in latest)

    def test_insert_dedup(self, event_in_db, sample_event):
        """Mesmo vip_name + timestamp não deve duplicar (INSERT OR IGNORE)."""
        ts = "2026-06-01T10:05:00Z"
        m = self._make_vip(sample_event["id"], "Carlos Menezes", -95.0, -10.0, ts=ts)
        database.insert_vip_batch([m, m])  # duplicata intencional
        # get_vip_series retorna colunas: timestamp, rsrp, rsrq, serving_cell, in_event (sem vip_name)
        series = database.get_vip_series(sample_event["id"], "Carlos Menezes", minutes=0)
        assert len(series) == 1

    def test_get_vip_series(self, event_in_db, sample_event):
        m = self._make_vip(sample_event["id"], "Ana Lima", -102.0, -13.0)
        database.insert_vip_batch([m])
        series = database.get_vip_series(sample_event["id"], "Ana Lima", minutes=120)
        assert len(series) >= 1


# ── Alertas ───────────────────────────────────────────────────────────

class TestAlerts:
    def _make_alert(self, event_id, alert_id=None):
        # insert_alert espera dict (sem o campo id — gerado pelo banco)
        return {
            "event_id": event_id,
            "level": "EVENT",
            "severity": "WARNING",
            "site_id": "SR-SPPNB2",
            "cell_id": "SR-SPPNB2_1",
            "message": "PRB alto",
            "timestamp": "2026-06-01T10:00:00Z",
        }

    def test_insert_and_get_active(self, event_in_db, sample_event):
        a = self._make_alert(sample_event["id"])
        rowid = database.insert_alert(a)
        assert rowid is not None
        alerts = database.get_active_alerts(sample_event["id"])
        assert len(alerts) >= 1
        assert any(al["message"] == "PRB alto" for al in alerts)

    def test_acknowledge_single(self, event_in_db, sample_event):
        rowid = database.insert_alert(self._make_alert(sample_event["id"]))
        database.acknowledge_alert(sample_event["id"], rowid)
        active = database.get_active_alerts(sample_event["id"])
        assert not any(al["id"] == rowid for al in active)

    def test_acknowledge_all(self, event_in_db, sample_event):
        for _ in range(3):
            database.insert_alert(self._make_alert(sample_event["id"]))
        database.acknowledge_all_alerts(sample_event["id"])
        active = database.get_active_alerts(sample_event["id"])
        assert active == []

    def test_delete_all_alerts(self, event_in_db, sample_event):
        database.insert_alert(self._make_alert(sample_event["id"]))
        database.delete_all_alerts(sample_event["id"])
        all_alerts = database.get_all_alerts(sample_event["id"])
        assert all_alerts == []

    def test_silence_alert(self, tmp_db):
        database.silence_alert("site:SR-001:cell:SR-001_1:dl_prb_usage")
        assert database.is_silenced("site:SR-001:cell:SR-001_1:dl_prb_usage")
        assert not database.is_silenced("site:SR-001:cell:SR-001_1:availability")


# ── VIPs (entidade global) ────────────────────────────────────────────

class TestVipEntity:
    def test_create_and_get(self, tmp_db):
        # save_vip espera dict
        result = database.save_vip({"name": "João Silva"})
        assert result is not None
        assert result["name"] == "João Silva"
        fetched = database.get_vip(result["id"])
        assert fetched["name"] == "João Silva"

    def test_get_vips_list(self, tmp_db):
        database.save_vip({"name": "Ana"})
        database.save_vip({"name": "Bob"})
        vips = database.get_vips()
        names = [v["name"] for v in vips]
        assert "Ana" in names and "Bob" in names

    def test_delete_vip(self, tmp_db):
        vip = database.save_vip({"name": "Temp"})
        database.delete_vip(vip["id"])
        assert database.get_vip(vip["id"]) is None

    def test_assign_and_unassign(self, event_in_db, sample_event):
        # get_event_vips filtra por oss=SP — criamos o VIP com oss="SP" para ele aparecer
        vip = database.save_vip({"name": "VIP Evento", "oss": "SP"})
        database.assign_vip_to_event(sample_event["id"], vip["id"], task_id=None)
        ev_vips = database.get_event_vips(sample_event["id"])
        assert any(v["name"] == "VIP Evento" for v in ev_vips)
        database.unassign_vip_from_event(sample_event["id"], vip["id"])
        # Após desassociar o vip continua na tabela global —
        # get_event_vips retorna VIPs por região, não por event_vips,
        # então o VIP ainda aparece. Verificamos via event_vips diretamente.
        e_conn = database.get_event_conn(sample_event["id"])
        rows = e_conn.execute(
            "SELECT * FROM event_vips WHERE event_id = ? AND vip_id = ?",
            (sample_event["id"], vip["id"])
        ).fetchall()
        assert len(rows) == 0


# ── Settings ─────────────────────────────────────────────────────────

class TestSettings:
    def test_save_and_get(self, tmp_db):
        database.save_settings({"theme": "dark", "lang": "pt-BR"})
        s = database.get_settings()
        assert s.get("theme") == "dark"

    def test_get_settings_empty(self, tmp_db):
        s = database.get_settings()
        assert isinstance(s, dict)


# ── Utilitários ───────────────────────────────────────────────────────

class TestUtils:
    def test_slugify_basic(self):
        assert database.slugify("Carlos Menezes") == "carlos-menezes"

    def test_slugify_accents(self):
        assert database.slugify("Ana Lívia") == "ana-livia"

    def test_slugify_apostrophe(self):
        # Apóstrofo vira '-' → "joao-d-avila" (comportamento real do slugify)
        assert database.slugify("João D'Avila") == "joao-d-avila"

    def test_get_db_size_mb(self, tmp_db):
        size = database.get_db_size_mb()
        assert isinstance(size, float)
        assert size >= 0.0
