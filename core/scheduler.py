"""
Scheduler: executa a coleta de dados em background a cada N segundos.
Roda em thread separada para não bloquear a interface.
"""

import logging
import threading
import time
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


class Scheduler:
    def __init__(self):
        self._collector: Optional[BaseCollector] = None
        self._threads: list = []
        self._stop_event = threading.Event()
        self._on_update: Optional[Callable] = None  # callback para notificar JS
        self._event_config: Optional[dict] = None
        self._recording: bool = False
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
        if self._recording:
            self.stop()

        self._event_config = event_config
        self._collector = build_collector(event_config, mock=mock)
        self._stop_event.clear()
        self._recording = True

        # Zera o estado das coletas ao (re)iniciar para não exibir dados do evento anterior.
        for k in ("kpi", "vip", "alarms"):
            interval = self._status[k]["interval_s"]
            extra = {key: value for key, value in self._status[k].items()
                     if key in {"mode", "vips_total", "vips_with_data"}}
            self._status[k] = {**self._new_status(interval), **extra}

        # VIPs iniciam primeiro; os KPIs (sites) entram com um pequeno atraso
        # inicial para garantir que a coleta de VIP arranque antes na abertura.
        t_vip = threading.Thread(
            target=self._loop, args=(self._collect_vips, INTERVAL_VIP_SECONDS),
            daemon=True, name="vip-collector"
        )
        t_kpi = threading.Thread(
            target=self._loop,
            args=(self._collect_kpis, INTERVAL_KPI_SECONDS, KPI_INITIAL_DELAY_SECONDS),
            daemon=True, name="kpi-collector"
        )
        t_alarms = threading.Thread(
            target=self._loop,
            args=(self._collect_alarms, INTERVAL_ALARMS_SECONDS, ALARMS_INITIAL_DELAY_SECONDS),
            daemon=True, name="alarms-collector"
        )
        self._threads = [t_vip, t_kpi, t_alarms]
        for t in self._threads:
            t.start()

        logger.info(f"Coleta iniciada para evento: {event_config['id']}")

    def stop(self):
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads = []
        self._recording = False
        logger.info("Coleta encerrada")

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _loop(self, fn: Callable, interval: int, initial_delay: int = 0):
        if initial_delay and self._stop_event.wait(initial_delay):
            return
        while not self._stop_event.is_set():
            try:
                fn()
                if self._on_update:
                    self._on_update()
            except Exception as e:
                logger.error(f"Erro na coleta: {e}")
            self._stop_event.wait(interval)

    def _mark_attempt(self, collector: str, **extra) -> None:
        self._status[collector].update({
            "state": "running",
            "last_attempt_at": datetime.utcnow().isoformat(),
            **extra,
        })

    @staticmethod
    def _insert_count(insert_response, attempted: int) -> int:
        """Aceita os retornos atuais (None) e futuros do repositório."""
        if isinstance(insert_response, int):
            return max(0, min(insert_response, attempted))
        if isinstance(insert_response, dict):
            return max(0, min(int(insert_response.get("inserted", attempted)), attempted))
        return attempted

    def _apply_result(self, collector: str, result: CollectionResult, started_at: float,
                      persist: Callable, evaluate: Optional[Callable] = None) -> None:
        if not isinstance(result, CollectionResult):
            raise TypeError(f"{collector} retornou {type(result).__name__}, esperado CollectionResult")
        measurements = result.measurements
        if measurements:
            inserted = self._insert_count(persist(measurements), len(measurements))
            result.inserted = inserted
            result.duplicate = max(result.duplicate, len(measurements) - inserted)
            if evaluate:
                evaluate(measurements)
        # Checkpoints pertencem ao lote: só avançam depois de a persistência
        # idempotente terminar, inclusive quando o lote é um replay completo.
        if result.cursors and collector in {"kpi", "vip"}:
            oss = ((self._event_config or {}).get("oss", {}).get("region") or "").upper()
            db.save_collection_checkpoints(
                (self._event_config or {}).get("id", ""), result.cursors,
                collector="monitoring" if collector == "kpi" else "vip", oss=oss,
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
            status["last_success"] = now  # cliente legado
        if result.state in {"data", "partial"} and (result.latest_data_at or measurements):
            status["last_data_at"] = result.latest_data_at or now

        status.update({
            "state": result.state,
            "last_count": len(measurements),
            "duration_s": round(time.time() - started_at, 2),
            **result.as_status_fields(),
        })
        # O resultado contém os contadores depois da inserção; restaura-os pois
        # as chaves de status foram expandidas pelo update acima.
        status["inserted"] = result.inserted
        status["duplicate"] = result.duplicate

    def _apply_unexpected_error(self, collector: str, error: Exception, started_at: float) -> None:
        # ``diagnostics`` é zerado porque a UI classifica a falha pelo ``code`` do
        # último diagnóstico; manter o do ciclo anterior descreveria outra falha.
        self._status[collector].update({
            "state": "error", "duration_s": round(time.time() - started_at, 2),
            "error": str(error), "cause": str(error), "diagnostics": [],
        })

    def _collect_kpis(self):
        if not self._collector:
            return
        self._mark_attempt("kpi")
        t0 = time.time()
        try:
            result = self._collector.collect_kpis()
            self._apply_result("kpi", result, t0, db.insert_kpi_batch, self._evaluate_kpi_alerts)
        except Exception as e:
            self._apply_unexpected_error("kpi", e, t0)
            raise

    def _collect_vips(self, mode: str = None) -> int:
        if not self._collector:
            return 0
        # Serializa para evitar coleta concorrente (ciclo agendado vs. refresh manual).
        with self._vip_lock:
            if mode is None:
                mode = "incremental"

            self._mark_attempt("vip", mode=mode)
            t0 = time.time()
            try:
                logger.info(f"Iniciando coleta de VIPs no modo: {mode}")
                result = self._collector.collect_vips(mode=mode)
                self._apply_result("vip", result, t0, db.insert_vip_batch, self._evaluate_vip_alerts)
                coverage = result.coverage
                self._status["vip"].update({
                    "vips_total": coverage.get("vips_configured", len(getattr(self._collector, "vips_by_task", {}) or {})),
                    "vips_with_data": coverage.get("vips_with_data", 0),
                })
                return result.inserted
            except Exception as e:
                self._apply_unexpected_error("vip", e, t0)
                raise

    def collect_vips_now(self) -> int:
        """Coleta de VIPs sob demanda (botão de refresh do painel). Retorna nº de medições."""
        count = self._collect_vips(mode="incremental")
        if self._on_update:
            try:
                self._on_update()
            except Exception:
                pass
        return count

    def _collect_alarms(self) -> int:
        """Coleta alarmes filtrados por tipo e grava no banco do evento (dedup por csn).

        Espelha _collect_kpis; serializado para nao rodar em paralelo com o refresh manual
        sobre a mesma sessao HTTP. Retorna o numero de alarmes coletados neste ciclo."""
        if not self._collector:
            return 0
        with self._alarms_lock:
            self._mark_attempt("alarms")
            t0 = time.time()
            try:
                result = self._collector.collect_alarms()
                self._apply_result("alarms", result, t0, db.insert_alarms_batch)
                return result.inserted
            except Exception as e:
                self._apply_unexpected_error("alarms", e, t0)
                raise

    def collect_alarms_now(self) -> int:
        """Coleta de alarmes sob demanda (refresh do painel / mudanca de filtro)."""
        count = self._collect_alarms()
        if self._on_update:
            try:
                self._on_update()
            except Exception:
                pass
        return count

    def _evaluate_kpi_alerts(self, measurements: list):
        if not self._event_config:
            return
        t = self._event_config.get("thresholds", {})
        crit = t.get("utilization_critical", 95)
        warn = t.get("utilization_warning", 80)
        avail_crit = t.get("availability_critical", 95)

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
                            "event_id":  self._event_config["id"],
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
                            "event_id":  self._event_config["id"],
                            "level":     "EVENT",
                            "severity":  "WARNING",
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "message":   f"Utilização{metric_label} elevada: {val:.0f}% em {site_id}",
                            "timestamp": timestamp,
                        })
                        seen[key] = True
            elif metric == "accessibility":
                if val < avail_crit:
                    key = f"avail_crit_{cell_id}"
                    if not db.is_silenced(key) and key not in seen:
                        db.insert_alert({
                            "event_id":  self._event_config["id"],
                            "level":     "EVENT",
                            "severity":  "CRITICAL",
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "message":   f"Acessibilidade crítica: {val:.1f}% na célula {cell_id}",
                            "timestamp": timestamp,
                        })
                        seen[key] = True

    def _evaluate_vip_alerts(self, measurements: list):
        if not self._event_config:
            return
        t = self._event_config.get("thresholds", {})
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
                        "event_id":  self._event_config["id"],
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
                        "event_id":  self._event_config["id"],
                        "level":     "EVENT",
                        "severity":  "WARNING",
                        "site_id":   m.get("serving_cell", ""),
                        "cell_id":   m.get("serving_cell", ""),
                        "message":   f"RSRP baixo para {vip}: {rsrp} dBm",
                        "timestamp": m["timestamp"],
                    })


# Instância global
scheduler = Scheduler()
