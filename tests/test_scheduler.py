"""
Testes para core/scheduler.py.

Testa a máquina de estados do Scheduler sem subir threads reais —
monkeypatchamos os collectors para retornar imediatamente.
"""
import time
import threading
import pytest
import core.database as db
from core.scheduler import CollectionContext, Scheduler
from core.collector import MockCollector
from core.collection_result import CollectionResult


@pytest.fixture
def scheduler(monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
    s = Scheduler()
    yield s
    if s.is_recording:
        s.stop()


class TestSchedulerState:
    @staticmethod
    def _install_context(scheduler, collector, event_id="event", oss="SP"):
        event = {"id": event_id, "oss": {"region": oss}, "thresholds": {}}
        context = CollectionContext(
            generation=1, event_id=event_id, oss=oss, collector=collector,
            event_config=event, stop_event=threading.Event(),
        )
        scheduler._generation = 1
        scheduler._active_context = context
        scheduler._collector = collector
        scheduler._event_config = event
        scheduler._recording = True
        return context

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

    @pytest.mark.parametrize("result_state", ["error", "auth_required", "partial"])
    def test_non_healthy_result_never_becomes_ok(self, scheduler, monkeypatch, result_state):
        class Collector:
            def collect_kpis(self):
                if result_state == "error":
                    return CollectionResult.error("HTTP 500")
                if result_state == "auth_required":
                    return CollectionResult.auth_required("401")
                return CollectionResult.partial([], cause="cobertura incompleta")

        self._install_context(scheduler, Collector())
        scheduler._collect_kpis()
        status = scheduler.get_status()["kpi"]
        assert status["state"] == result_state
        assert status["last_cycle_ok_at"] is None

    def test_empty_is_valid_cycle_but_does_not_update_last_data(self, scheduler):
        class Collector:
            def collect_kpis(self):
                return CollectionResult.empty("sem novidade")

        self._install_context(scheduler, Collector())
        scheduler._collect_kpis()
        status = scheduler.get_status()["kpi"]
        assert status["state"] == "empty"
        assert status["last_cycle_ok_at"] is not None
        assert status["last_data_at"] is None

    def test_kpi_nao_persiste_cursor_quando_tudo_foi_descartado(self, scheduler, monkeypatch):
        saved = []
        monkeypatch.setattr(db, "save_collection_checkpoints", lambda *args, **kwargs: saved.append((args, kwargs)))
        context = self._install_context(scheduler, object())
        result = CollectionResult.partial(
            [], cause="cobertura incompleta",
            cursors={"10:": {"task_id": 10, "object_key": "", "cursor": 123}},
            coverage={"unmapped_objects": 1},
        )

        scheduler._apply_result("kpi", result, time.time(), lambda rows: None,
                                context=context)

        assert saved == []

    def test_kpi_vazio_legitimo_persiste_cursor(self, scheduler, monkeypatch):
        saved = []
        monkeypatch.setattr(db, "save_collection_checkpoints", lambda *args, **kwargs: saved.append((args, kwargs)))
        context = self._install_context(scheduler, object())
        cursors = {"10:": {"task_id": 10, "object_key": "", "cursor": 123}}
        result = CollectionResult.empty("sem novidade", cursors=cursors,
                                        coverage={"unmapped_objects": 0})

        scheduler._apply_result("kpi", result, time.time(), lambda rows: None,
                                context=context)

        assert len(saved) == 1
        assert saved[0][0][1] == cursors

    def test_resultado_obsoleto_nao_persiste_alerta_checkpoint_ou_status(
            self, scheduler, monkeypatch):
        persisted = []
        checkpoints = []
        alerts = []
        monkeypatch.setattr(db, "save_collection_checkpoints",
                            lambda *a, **k: checkpoints.append((a, k)))
        monkeypatch.setattr(db, "insert_alert", lambda row: alerts.append(row))

        old_context = self._install_context(scheduler, object(), event_id="A", oss="SP")
        old_context.stop_event.set()
        new_context = CollectionContext(
            generation=2, event_id="B", oss="OUTRAS", collector=object(),
            event_config={"id": "B", "oss": {"region": "OUTRAS"}, "thresholds": {}},
            stop_event=threading.Event(),
        )
        scheduler._generation = 2
        scheduler._active_context = new_context
        scheduler._event_config = new_context.event_config
        scheduler._status["kpi"]["state"] = "running"
        result = CollectionResult.data(
            [{"event_id": "A", "site_id": "S", "cell_id": "C", "metric": "x",
              "value": 1, "timestamp": "2026-08-13T00:00:00Z"}],
            cursors={"10:": {"task_id": 10, "object_key": "", "cursor": 123}},
        )

        applied = scheduler._apply_result(
            "kpi", result, time.time(), lambda rows: persisted.extend(rows),
            scheduler._evaluate_kpi_alerts, context=old_context,
        )

        assert applied is False
        assert persisted == checkpoints == alerts == []
        assert scheduler.get_status()["kpi"]["state"] == "running"

    def test_stop_start_usa_evento_de_parada_novo_e_nao_reanima_worker_antigo(
            self, sample_event, monkeypatch):
        import core.scheduler as scheduler_module

        entered = threading.Event()
        release = threading.Event()

        class BlockingCollector:
            def __init__(self, event):
                self.event = event
                self.calls = 0
                self.cancel_event = None
            def set_cancel_event(self, cancel_event):
                self.cancel_event = cancel_event
            def start(self):
                pass
            def stop(self):
                pass
            def collect_vips(self, mode="incremental"):
                self.calls += 1
                entered.set()
                release.wait(2)
                return CollectionResult.empty("fim")
            def collect_kpis(self):
                return CollectionResult.empty("fim")
            def collect_alarms(self):
                return CollectionResult.empty("fim")

        class EmptyCollector(BlockingCollector):
            def collect_vips(self, mode="incremental"):
                self.calls += 1
                return CollectionResult.empty("fim")

        event_b = {**sample_event, "id": "event-b",
                   "oss": {**sample_event["oss"], "region": "OUTRAS"}}
        collectors = [BlockingCollector(sample_event), EmptyCollector(event_b)]
        monkeypatch.setattr(scheduler_module, "build_collector", lambda *a, **k: collectors.pop(0))
        monkeypatch.setattr(scheduler_module, "STOP_JOIN_TIMEOUT_SECONDS", 0.01)

        scheduler = Scheduler()
        scheduler.start(sample_event, mock=True)
        old_context = scheduler._active_context
        assert entered.wait(1)
        scheduler.start(event_b, mock=True)
        new_context = scheduler._active_context
        release.set()
        time.sleep(0.05)

        assert old_context.stop_event.is_set()
        assert old_context.stop_event is not new_context.stop_event
        assert not new_context.stop_event.is_set()
        assert old_context.collector.cancel_event is old_context.stop_event
        assert new_context.collector.cancel_event is new_context.stop_event
        assert old_context.collector.calls == 1
        scheduler.stop()


class TestKpiAlertSampleFloor:
    """B7 — acessibilidade sobre pouquíssimas tentativas não é degradação.

    Um único sucesso em duas tentativas dá 50,0% e disparava CRITICAL; 454 dos
    alertas do evento tinham essa assinatura (50,0%, 66,7%, 80,0%).
    """

    @staticmethod
    def _context(thresholds=None):
        event = {"id": "event", "oss": {"region": "SP"}, "thresholds": thresholds or {}}
        return CollectionContext(
            generation=1, event_id="event", oss="SP", collector=object(),
            event_config=event, stop_event=threading.Event(),
        )

    @staticmethod
    def _measurement(sample_size, value=50.0):
        row = {"metric": "accessibility", "site_id": "SITE", "cell_id": "4G-CELL",
               "value": value, "timestamp": "2026-08-21T11:10:00Z"}
        if sample_size is not None:
            row["sample_size"] = sample_size
        return row

    def _alerts_for(self, scheduler, monkeypatch, measurement, thresholds=None):
        alerts = []
        monkeypatch.setattr(db, "is_silenced", lambda *_: False)
        monkeypatch.setattr(db, "insert_alert", lambda row: alerts.append(row))
        scheduler._evaluate_kpi_alerts([measurement], self._context(thresholds))
        return alerts

    def test_accessibility_alert_requires_minimum_sample(self, scheduler, monkeypatch):
        assert self._alerts_for(scheduler, monkeypatch, self._measurement(2)) == []
        assert len(self._alerts_for(scheduler, monkeypatch, self._measurement(40))) == 1

    def test_minimum_sample_is_configurable(self, scheduler, monkeypatch):
        alerts = self._alerts_for(scheduler, monkeypatch, self._measurement(2),
                                  thresholds={"alert_min_samples": 2})
        assert len(alerts) == 1

    def test_measurement_without_sample_size_still_alerts(self, scheduler, monkeypatch):
        """Séries antigas e coletores que não informam a amostra não emudecem."""
        alerts = self._alerts_for(scheduler, monkeypatch, self._measurement(None))
        assert len(alerts) == 1

    def test_healthy_accessibility_never_alerts(self, scheduler, monkeypatch):
        alerts = self._alerts_for(scheduler, monkeypatch,
                                  self._measurement(500, value=99.9))
        assert alerts == []


class TestKpiAlertThresholdObjectFormat:
    """B2 — thresholds podem estar gravados como número cru (legado) ou como
    {"value", "unit"}. O alarme em tempo real não pode parar de disparar
    silenciosamente quando o cadastro passa a gravar o novo formato."""

    @staticmethod
    def _context(thresholds):
        event = {"id": "event", "oss": {"region": "SP"}, "thresholds": thresholds}
        return CollectionContext(
            generation=1, event_id="event", oss="SP", collector=object(),
            event_config=event, stop_event=threading.Event(),
        )

    @staticmethod
    def _utilization_measurement(value):
        return {"metric": "utilization_dl", "site_id": "SITE", "cell_id": "4G-CELL",
                "value": value, "timestamp": "2026-08-21T11:10:00Z"}

    def test_utilization_alert_fires_with_object_shaped_threshold(self, scheduler, monkeypatch):
        alerts = []
        monkeypatch.setattr(db, "is_silenced", lambda *_: False)
        monkeypatch.setattr(db, "insert_alert", lambda row: alerts.append(row))
        thresholds = {"utilization_critical": {"value": 95, "unit": "%"},
                      "utilization_warning": {"value": 80, "unit": "%"}}
        scheduler._evaluate_kpi_alerts(
            [self._utilization_measurement(97.0)], self._context(thresholds))
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "CRITICAL"

    def test_utilization_alert_stays_silent_below_object_shaped_threshold(self, scheduler, monkeypatch):
        alerts = []
        monkeypatch.setattr(db, "is_silenced", lambda *_: False)
        monkeypatch.setattr(db, "insert_alert", lambda row: alerts.append(row))
        thresholds = {"utilization_critical": {"value": 95, "unit": "%"},
                      "utilization_warning": {"value": 80, "unit": "%"}}
        scheduler._evaluate_kpi_alerts(
            [self._utilization_measurement(50.0)], self._context(thresholds))
        assert alerts == []

    def test_vip_rsrp_alert_fires_with_object_shaped_threshold(self, scheduler, monkeypatch):
        alerts = []
        monkeypatch.setattr(db, "is_silenced", lambda *_: False)
        monkeypatch.setattr(db, "insert_alert", lambda row: alerts.append(row))
        thresholds = {"rsrp_critical": {"value": -110, "unit": "dBm"},
                      "rsrp_warning": {"value": -100, "unit": "dBm"}}
        measurement = {"vip_name": "VIP", "in_event": True, "rsrp": -115,
                       "serving_cell": "4G-CELL", "timestamp": "2026-08-21T11:10:00Z"}
        scheduler._evaluate_vip_alerts([measurement], self._context(thresholds))
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "CRITICAL"


class TestAlarmReconciliation:
    """O alarme limpo no iManager some da coleta; sumir e a unica evidencia.
    Reconciliar so pode acontecer num ciclo integro e saudavel."""

    @staticmethod
    def _install(scheduler, result):
        class Collector:
            def collect_alarms(self):
                return result

        event = {"id": "event", "oss": {"region": "SP"}, "thresholds": {}}
        context = CollectionContext(
            generation=1, event_id="event", oss="SP", collector=Collector(),
            event_config=event, stop_event=threading.Event(),
        )
        scheduler._generation = 1
        scheduler._active_context = context
        scheduler._collector = context.collector
        scheduler._event_config = event
        scheduler._recording = True
        return context

    @staticmethod
    def _spy(monkeypatch):
        calls = []
        monkeypatch.setattr(db, "insert_alarms_batch", lambda rows: None)
        monkeypatch.setattr(db, "reconcile_alarms",
                            lambda event_id, active, cleared_at: calls.append(
                                (event_id, set(active), cleared_at)) or len(active))
        return calls

    @staticmethod
    def _alarm(csn):
        return {"csn": csn, "event_id": "event", "alarm_name": "Cell Unavailable",
                "severity": "Major", "arrive_time": "2026-06-30T10:00:00Z",
                "collected_at": "2026-06-30T10:00:00Z"}

    def test_ciclo_completo_reconcilia(self, scheduler, monkeypatch):
        calls = self._spy(monkeypatch)
        self._install(scheduler, CollectionResult.data(
            [self._alarm(1), self._alarm(2)], coverage={"complete": True}))

        scheduler._collect_alarms()

        assert len(calls) == 1
        assert calls[0][0] == "event"
        assert calls[0][1] == {1, 2}
        assert calls[0][2]  # carimbo de limpeza preenchido

    def test_ciclo_vazio_reconcilia(self, scheduler, monkeypatch):
        """Rede sem nenhum alarme ativo: sem isso, limpar o ultimo deixaria a
        lista antiga na tela para sempre."""
        calls = self._spy(monkeypatch)
        self._install(scheduler, CollectionResult.empty(
            "sem alarmes", coverage={"complete": True}))

        scheduler._collect_alarms()

        assert len(calls) == 1
        assert calls[0][1] == set()

    def test_ciclo_incompleto_nao_reconcilia(self, scheduler, monkeypatch):
        """Paginacao interrompida nao pode limpar o painel inteiro."""
        calls = self._spy(monkeypatch)
        self._install(scheduler, CollectionResult.data(
            [self._alarm(1)], coverage={"complete": False}))

        scheduler._collect_alarms()

        assert calls == []

    def test_ciclo_sem_sinal_de_integridade_nao_reconcilia(self, scheduler, monkeypatch):
        """Coletor sem nocao do estado da rede (CSV/Null) nunca limpa a tela."""
        calls = self._spy(monkeypatch)
        self._install(scheduler, CollectionResult.empty("sem fonte"))

        scheduler._collect_alarms()

        assert calls == []

    @pytest.mark.parametrize("result_state", ["error", "auth_required"])
    def test_ciclo_com_erro_nao_reconcilia(self, scheduler, monkeypatch, result_state):
        calls = self._spy(monkeypatch)
        result = (CollectionResult.error("HTTP 500", coverage={"complete": True})
                  if result_state == "error"
                  else CollectionResult.auth_required("401", coverage={"complete": True}))
        self._install(scheduler, result)

        scheduler._collect_alarms()

        assert calls == []
        assert scheduler.get_status()["alarms"]["state"] == result_state

    def test_reconcilia_depois_de_persistir(self, scheduler, monkeypatch):
        """A ordem importa: reconciliar antes do upsert marcaria como limpo
        justamente o alarme que acabou de chegar."""
        ordem = []
        monkeypatch.setattr(db, "insert_alarms_batch", lambda rows: ordem.append("persist"))
        monkeypatch.setattr(db, "reconcile_alarms",
                            lambda *a, **k: ordem.append("reconcile") or 0)
        self._install(scheduler, CollectionResult.data(
            [self._alarm(1)], coverage={"complete": True}))

        scheduler._collect_alarms()

        assert ordem == ["persist", "reconcile"]

    def test_resultado_obsoleto_nao_reconcilia(self, scheduler, monkeypatch):
        """Troca de evento no meio do ciclo: o lote inteiro e descartado."""
        calls = self._spy(monkeypatch)
        context = self._install(scheduler, CollectionResult.data(
            [self._alarm(1)], coverage={"complete": True}))
        context.stop_event.set()

        scheduler._collect_alarms()

        assert calls == []
