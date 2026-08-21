"""
Scheduler: executa a coleta de dados em background a cada N segundos.
Roda em thread separada para não bloquear a interface.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from core import database as db
from core.collection_result import CollectionResult
from core.collector import BaseCollector, build_collector

logger = logging.getLogger(__name__)

INTERVAL_KPI_SECONDS = 120  # OSS: ciclo de 2 minutos
INTERVAL_VIP_SECONDS = 60   # Trace: 1 minuto por VIP
INTERVAL_VIP_FULL_SECONDS = 600  # Ciclo completo de VIP a cada 10 minutos
INTERVAL_ALARMS_SECONDS = 180  # FM: ciclo de alarmes a cada 3 minutos
KPI_INITIAL_DELAY_SECONDS = 5  # Atraso inicial dos KPIs para os VIPs iniciarem primeiro
ALARMS_INITIAL_DELAY_SECONDS = 10  # Alarmes entram após VIPs/KPIs no arranque
STOP_JOIN_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CollectionContext:
    """Identidade imutável de uma execução do scheduler."""

    generation: int
    event_id: str
    oss: str
    collector: BaseCollector
    event_config: dict
    stop_event: threading.Event


class Scheduler:
    def __init__(self):
        self._collector: Optional[BaseCollector] = None
        self._threads: list = []
        self._stop_event = threading.Event()  # compatibilidade; substituído a cada start()
        self._on_update: Optional[Callable] = None  # callback para notificar JS
        self._event_config: Optional[dict] = None
        self._recording: bool = False
        self._generation: int = 0
        self._active_context: Optional[CollectionContext] = None
        # Transições não se intercalam. O state lock forma a fronteira atômica
        # entre invalidar uma geração e persistir um resultado dela.
        self._transition_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._kpi_lock = threading.Lock()
        # Serializa coletas de VIP (ciclo agendado vs. refresh sob demanda) para
        # não rodarem em paralelo sobre a mesma sessão HTTP do iManager.
        self._vip_lock = threading.Lock()
        # Serializa coletas de alarmes (ciclo agendado vs. refresh sob demanda / mudança
        # de filtro) para não rodarem em paralelo sobre a mesma sessão HTTP.
        self._alarms_lock = threading.Lock()
        self._last_vip_full_time = 0.0  # timestamp da última coleta VIP completa
        # Estado operacional. ``last_cycle_ok_at`` e ``last_data_at`` têm
        # significados distintos: uma resposta vazia válida não inventa dado novo.
        self._status = {
            "kpi": {
                **self._new_status(INTERVAL_KPI_SECONDS),
            },
            "vip": {
                **self._new_status(INTERVAL_VIP_SECONDS),
                "mode": None, "vips_total": 0, "vips_with_data": 0,
            },
            "alarms": {
                **self._new_status(INTERVAL_ALARMS_SECONDS),
            },
        }

    @staticmethod
    def _new_status(interval_s: int) -> dict:
        return {
            "state": "idle", "last_attempt_at": None, "last_cycle_ok_at": None,
            "last_data_at": None, "last_success": None,  # compatibilidade com clientes antigos
            "last_count": 0, "received": 0, "calculated": 0, "invalid": 0,
            "not_applicable": 0,
            "duplicate": 0, "inserted": 0, "coverage": {}, "diagnostics": [],
            "duration_s": None, "error": None, "cause": None, "interval_s": interval_s,
        }

    def get_status(self) -> dict:
        """Snapshot do estado das coletas (KPI/VIP/alarmes) para o indicador de sincronização."""
        return {key: self._status_snapshot(value) for key, value in self._status.items()}

    @staticmethod
    def _status_snapshot(status: dict) -> dict:
        snapshot = dict(status)
        state = snapshot.get("state")
        if state in {"data", "empty"} and snapshot.get("last_data_at"):
            try:
                age_s = (datetime.utcnow() - datetime.fromisoformat(
                    snapshot["last_data_at"].replace("Z", "+00:00").replace("+00:00", "")
                )).total_seconds()
                if age_s > max(snapshot.get("interval_s", 0) * 2, 1):
                    snapshot["state"] = "stale"
            except (TypeError, ValueError):
                pass
        return snapshot

    def set_update_callback(self, fn: Callable):
        """Define função chamada após cada coleta bem-sucedida."""
        self._on_update = fn

    def start(self, event_config: dict, mock: bool = False):
        """Inicia coleta para o evento fornecido."""
        with self._transition_lock:
            # Invalida a geração anterior antes de aguardar seus workers. Mesmo que
            # uma requisição exceda o join, o resultado tardio já será obsoleto.
            self.stop()
            collector = build_collector(event_config, mock=mock)
            stop_event = threading.Event()
            with self._state_lock:
                self._generation += 1
                context = CollectionContext(
                    generation=self._generation,
                    event_id=event_config["id"],
                    oss=((event_config.get("oss") or {}).get("region") or "").upper(),
                    collector=collector,
                    event_config=event_config,
                    stop_event=stop_event,
                )
                self._active_context = context
                self._event_config = event_config
                self._collector = collector
                self._stop_event = stop_event
                self._recording = True
                if hasattr(collector, "set_cancel_event"):
                    collector.set_cancel_event(stop_event)
                collector.start()

                # Zera o estado ao iniciar para não exibir dados do evento anterior.
                for k in ("kpi", "vip", "alarms"):
                    interval = self._status[k]["interval_s"]
                    extra = {key: value for key, value in self._status[k].items()
                             if key in {"mode", "vips_total", "vips_with_data"}}
                    self._status[k] = {**self._new_status(interval), **extra}

            t_vip = threading.Thread(
                target=self._loop,
                args=(context, "vip", self._collect_vips, INTERVAL_VIP_SECONDS),
                daemon=True, name=f"vip-collector-g{context.generation}",
            )
            t_kpi = threading.Thread(
                target=self._loop,
                args=(context, "kpi", self._collect_kpis, INTERVAL_KPI_SECONDS,
                      KPI_INITIAL_DELAY_SECONDS),
                daemon=True, name=f"kpi-collector-g{context.generation}",
            )
            t_alarms = threading.Thread(
                target=self._loop,
                args=(context, "alarms", self._collect_alarms, INTERVAL_ALARMS_SECONDS,
                      ALARMS_INITIAL_DELAY_SECONDS),
                daemon=True, name=f"alarms-collector-g{context.generation}",
            )
            self._threads = [t_vip, t_kpi, t_alarms]
            for thread in self._threads:
                thread.start()

            logger.info(
                "Coleta iniciada para evento %s (OSS=%s, geração=%s)",
                context.event_id, context.oss or "N/D", context.generation,
            )

    def stop(self):
        with self._transition_lock:
            with self._state_lock:
                context = self._active_context
                threads = list(self._threads)
                if context is not None:
                    context.stop_event.set()
                    if hasattr(context.collector, "stop"):
                        context.collector.stop()
                self._active_context = None
                self._threads = []
                self._recording = False

            deadline = time.monotonic() + STOP_JOIN_TIMEOUT_SECONDS
            for thread in threads:
                remaining = max(0.0, deadline - time.monotonic())
                thread.join(timeout=remaining)
            alive = [thread.name for thread in threads if thread.is_alive()]
            if alive:
                logger.warning(
                    "Geração %s invalidada; workers ainda finalizando serão descartados: %s",
                    getattr(context, "generation", "?"), ", ".join(alive),
                )
            elif context is not None:
                logger.info("Coleta encerrada (geração=%s)", context.generation)

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _current_context(self) -> Optional[CollectionContext]:
        with self._state_lock:
            return self._active_context

    def _is_current_unlocked(self, context: CollectionContext) -> bool:
        return self._active_context is context and not context.stop_event.is_set()

    def _is_current(self, context: CollectionContext) -> bool:
        with self._state_lock:
            return self._is_current_unlocked(context)

    def _loop(self, context: CollectionContext, collector_name: str, fn: Callable,
              interval: int, initial_delay: int = 0):
        if initial_delay and context.stop_event.wait(initial_delay):
            return
        while not context.stop_event.is_set():
            try:
                fn(context)
                if self._is_current(context) and self._on_update:
                    self._on_update()
            except Exception as e:
                if self._is_current(context):
                    logger.error(
                        "Erro na coleta %s (evento=%s, geração=%s): %s",
                        collector_name, context.event_id, context.generation, e,
                    )
            context.stop_event.wait(interval)

    def _mark_attempt(self, context: CollectionContext, collector: str, **extra) -> bool:
        with self._state_lock:
            if not self._is_current_unlocked(context):
                return False
            self._status[collector].update({
                "state": "running",
                "last_attempt_at": datetime.utcnow().isoformat(),
                **extra,
            })
            return True

    @staticmethod
    def _insert_count(insert_response, attempted: int) -> int:
        """Aceita os retornos atuais (None) e futuros do repositório."""
        if isinstance(insert_response, int):
            return max(0, min(insert_response, attempted))
        if isinstance(insert_response, dict):
            return max(0, min(int(insert_response.get("inserted", attempted)), attempted))
        return attempted

    def _apply_result(self, collector: str, result: CollectionResult, started_at: float,
                      persist: Callable, evaluate: Optional[Callable] = None,
                      context: Optional[CollectionContext] = None) -> bool:
        if not isinstance(result, CollectionResult):
            raise TypeError(f"{collector} retornou {type(result).__name__}, esperado CollectionResult")
        context = context or self._current_context()
        if context is None:
            return False

        # Mantém o lock durante persistência, alertas, checkpoint e status. Uma
        # transição concorrente só pode ocorrer antes deste bloco (descarta tudo)
        # ou depois dele (o lote terminou integralmente no evento correto).
        with self._state_lock:
            if not self._is_current_unlocked(context):
                logger.info(
                    "Resultado %s descartado (evento=%s, OSS=%s, geração=%s obsoleta)",
                    collector, context.event_id, context.oss or "N/D", context.generation,
                )
                return False

            measurements = result.measurements
            if measurements:
                inserted = self._insert_count(persist(measurements), len(measurements))
                result.inserted = inserted
                result.duplicate = max(result.duplicate, len(measurements) - inserted)
                if evaluate:
                    evaluate(measurements, context)
            # Checkpoints pertencem ao lote e usam a identidade capturada pelo worker.
            discarded_all = (
                collector == "kpi"
                and not measurements
                and result.coverage.get("unmapped_objects", 0) > 0
            )
            if result.cursors and collector in {"kpi", "vip"} and not discarded_all:
                db.save_collection_checkpoints(
                    context.event_id, result.cursors,
                    collector="monitoring" if collector == "kpi" else "vip",
                    oss=context.oss,
                )

            now = datetime.utcnow().isoformat()
            status = self._status[collector]
            healthy = result.state in {"data", "empty"}
            if healthy:
                status["error"] = None
            elif result.state in {"error", "auth_required"}:
                status["error"] = result.cause

            if healthy:
                status["last_cycle_ok_at"] = now
                status["last_success"] = now
            if result.state in {"data", "partial"} and (result.latest_data_at or measurements):
                status["last_data_at"] = result.latest_data_at or now

            status.update({
                "state": result.state,
                "last_count": len(measurements),
                "duration_s": round(time.time() - started_at, 2),
                **result.as_status_fields(),
            })
            status["inserted"] = result.inserted
            status["duplicate"] = result.duplicate
            return True

    def _apply_unexpected_error(self, context: CollectionContext, collector: str,
                                error: Exception, started_at: float) -> None:
        with self._state_lock:
            if not self._is_current_unlocked(context):
                return
            self._status[collector].update({
                "state": "error", "duration_s": round(time.time() - started_at, 2),
                "error": str(error), "cause": str(error), "diagnostics": [],
            })

    def _collect_kpis(self, context: Optional[CollectionContext] = None):
        context = context or self._current_context()
        if context is None:
            return 0
        with self._kpi_lock:
            if not self._is_current(context) or not self._mark_attempt(context, "kpi"):
                return 0
            t0 = time.time()
            try:
                result = context.collector.collect_kpis()
                applied = self._apply_result(
                    "kpi", result, t0, db.insert_kpi_batch, self._evaluate_kpi_alerts,
                    context=context,
                )
                return result.inserted if applied else 0
            except Exception as e:
                self._apply_unexpected_error(context, "kpi", e, t0)
                raise

    def _collect_vips(self, context: Optional[CollectionContext] = None,
                      mode: str = None) -> int:
        context = context or self._current_context()
        if context is None:
            return 0
        # Serializa para evitar coleta concorrente (ciclo agendado vs. refresh manual).
        with self._vip_lock:
            if not self._is_current(context):
                return 0
            if mode is None:
                mode = "incremental"

            if not self._mark_attempt(context, "vip", mode=mode):
                return 0
            t0 = time.time()
            try:
                logger.info(f"Iniciando coleta de VIPs no modo: {mode}")
                result = context.collector.collect_vips(mode=mode)
                applied = self._apply_result(
                    "vip", result, t0, db.insert_vip_batch, self._evaluate_vip_alerts,
                    context=context,
                )
                if not applied:
                    return 0
                coverage = result.coverage
                with self._state_lock:
                    if self._is_current_unlocked(context):
                        self._status["vip"].update({
                            "vips_total": coverage.get(
                                "vips_configured",
                                len(getattr(context.collector, "vips_by_task", {}) or {}),
                            ),
                            "vips_with_data": coverage.get("vips_with_data", 0),
                        })
                return result.inserted
            except Exception as e:
                self._apply_unexpected_error(context, "vip", e, t0)
                raise

    def collect_vips_now(self) -> int:
        """Coleta de VIPs sob demanda (botão de refresh do painel). Retorna nº de medições."""
        context = self._current_context()
        if context is None:
            return 0
        count = self._collect_vips(context, mode="incremental")
        if self._is_current(context) and self._on_update:
            try:
                self._on_update()
            except Exception:
                pass
        return count

    def _collect_alarms(self, context: Optional[CollectionContext] = None) -> int:
        """Coleta alarmes filtrados por tipo e grava no banco do evento (dedup por csn).

        Espelha _collect_kpis; serializado para nao rodar em paralelo com o refresh manual
        sobre a mesma sessao HTTP. Retorna o numero de alarmes coletados neste ciclo."""
        context = context or self._current_context()
        if context is None:
            return 0
        with self._alarms_lock:
            if not self._is_current(context) or not self._mark_attempt(context, "alarms"):
                return 0
            t0 = time.time()
            try:
                result = context.collector.collect_alarms()
                applied = self._apply_result(
                    "alarms", result, t0, db.insert_alarms_batch, context=context)
                return result.inserted if applied else 0
            except Exception as e:
                self._apply_unexpected_error(context, "alarms", e, t0)
                raise

    def collect_alarms_now(self) -> int:
        """Coleta de alarmes sob demanda (refresh do painel / mudanca de filtro)."""
        context = self._current_context()
        if context is None:
            return 0
        count = self._collect_alarms(context)
        if self._is_current(context) and self._on_update:
            try:
                self._on_update()
            except Exception:
                pass
        return count

    def _evaluate_kpi_alerts(self, measurements: list, context: CollectionContext):
        t = context.event_config.get("thresholds", {})
        crit = t.get("utilization_critical", 95)
        warn = t.get("utilization_warning", 80)
        avail_crit = t.get("availability_critical", 95)
        # B7 — acessibilidade calculada sobre pouquíssimas tentativas é ruído, não
        # degradação: 1 de 2 vira 50,0% e disparava CRITICAL. Só alarma com amostra.
        min_samples = t.get("alert_min_samples", 20)

        seen = {}
        for m in measurements:
            metric = m["metric"]
            site_id = m["site_id"]
            val = m["value"]
            cell_id = m["cell_id"]
            timestamp = m["timestamp"]

            if metric in ("utilization", "utilization_dl"):
                metric_label = " DL"

                if val >= crit:
                    key = f"util_crit_{site_id}"
                    if not db.is_silenced(key) and key not in seen:
                        db.insert_alert({
                            "event_id":  context.event_id,
                            "level":     "EVENT",
                            "severity":  "CRITICAL",
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "message":   f"Utilização{metric_label} crítica: {val:.0f}% em {site_id}",
                            "timestamp": timestamp,
                        })
                        seen[key] = True
                elif val >= warn:
                    key = f"util_warn_{site_id}"
                    if not db.is_silenced(key) and key not in seen:
                        db.insert_alert({
                            "event_id":  context.event_id,
                            "level":     "EVENT",
                            "severity":  "WARNING",
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "message":   f"Utilização{metric_label} elevada: {val:.0f}% em {site_id}",
                            "timestamp": timestamp,
                        })
                        seen[key] = True
            elif metric == "accessibility":
                samples = m.get("sample_size")
                if samples is not None and samples < min_samples:
                    continue
                if val < avail_crit:
                    key = f"avail_crit_{cell_id}"
                    if not db.is_silenced(key) and key not in seen:
                        db.insert_alert({
                            "event_id":  context.event_id,
                            "level":     "EVENT",
                            "severity":  "CRITICAL",
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "message":   f"Acessibilidade crítica: {val:.1f}% na célula {cell_id}",
                            "timestamp": timestamp,
                        })
                        seen[key] = True

    def _evaluate_vip_alerts(self, measurements: list, context: CollectionContext):
        t = context.event_config.get("thresholds", {})
        rsrp_crit = t.get("rsrp_critical", -110)
        rsrp_warn = t.get("rsrp_warning", -100)

        for m in measurements:
            if not m.get("in_event"):
                continue
            rsrp = m.get("rsrp")
            if rsrp is None:
                continue
            vip = m["vip_name"]
            if rsrp <= rsrp_crit:
                key = f"rsrp_crit_{vip}"
                if not db.is_silenced(key):
                    db.insert_alert({
                        "event_id":  context.event_id,
                        "level":     "EVENT",
                        "severity":  "CRITICAL",
                        "site_id":   m.get("serving_cell", ""),
                        "cell_id":   m.get("serving_cell", ""),
                        "message":   f"RSRP crítico para {vip}: {rsrp} dBm",
                        "timestamp": m["timestamp"],
                    })
            elif rsrp <= rsrp_warn:
                key = f"rsrp_warn_{vip}"
                if not db.is_silenced(key):
                    db.insert_alert({
                        "event_id":  context.event_id,
                        "level":     "EVENT",
                        "severity":  "WARNING",
                        "site_id":   m.get("serving_cell", ""),
                        "cell_id":   m.get("serving_cell", ""),
                        "message":   f"RSRP baixo para {vip}: {rsrp} dBm",
                        "timestamp": m["timestamp"],
                    })


# Instância global
scheduler = Scheduler()
