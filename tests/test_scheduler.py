"""
Testes para core/scheduler.py.

Testa a máquina de estados do Scheduler sem subir threads reais —
monkeypatchamos os collectors para retornar imediatamente.
"""
import time
import threading
import pytest
import core.database as db
from core.scheduler import Scheduler
from core.collector import MockCollector


@pytest.fixture
def scheduler(monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
    s = Scheduler()
    yield s
    if s.is_recording:
        s.stop()


class TestSchedulerState:
    def test_initial_state_not_recording(self, scheduler):
        assert not scheduler.is_recording

    def test_start_sets_recording(self, scheduler, event_in_db, sample_event, monkeypatch):
        monkeypatch.setattr(db, "insert_kpi_batch", lambda *a, **k: None)
        monkeypatch.setattr(db, "insert_vip_batch", lambda *a, **k: None)
        monkeypatch.setattr(db, "insert_alert", lambda *a, **k: None)
        scheduler.start(sample_event, mock=True)
        assert scheduler.is_recording
        scheduler.stop()

    def test_stop_clears_recording(self, scheduler, event_in_db, sample_event, monkeypatch):
        monkeypatch.setattr(db, "insert_kpi_batch", lambda *a, **k: None)
        monkeypatch.setattr(db, "insert_vip_batch", lambda *a, **k: None)
        monkeypatch.setattr(db, "insert_alert", lambda *a, **k: None)
        scheduler.start(sample_event, mock=True)
        scheduler.stop()
        assert not scheduler.is_recording

    def test_get_status_structure(self, scheduler):
        status = scheduler.get_status()
        assert "kpi" in status
        assert "vip" in status

    def test_double_stop_is_safe(self, scheduler, event_in_db, sample_event, monkeypatch):
        monkeypatch.setattr(db, "insert_kpi_batch", lambda *a, **k: None)
        monkeypatch.setattr(db, "insert_vip_batch", lambda *a, **k: None)
        monkeypatch.setattr(db, "insert_alert", lambda *a, **k: None)
        scheduler.start(sample_event, mock=True)
        scheduler.stop()
        scheduler.stop()  # não deve levantar exceção
        assert not scheduler.is_recording
