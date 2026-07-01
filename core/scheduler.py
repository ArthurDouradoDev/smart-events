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
        # Estado das coletas para o indicador de sincronização do frontend.
        # state: "idle" | "running" | "ok" | "error".
        self._status = {
            "kpi": {
                "state": "idle", "last_success": None, "last_count": 0,
                "duration_s": None, "error": None, "interval_s": INTERVAL_KPI_SECONDS,
            },
            "vip": {
                "state": "idle", "last_success": None, "last_count": 0,
                "duration_s": None, "error": None, "interval_s": INTERVAL_VIP_SECONDS,
                "mode": None, "vips_total": 0, "vips_with_data": 0,
            },
            "alarms": {
                "state": "idle", "last_success": None, "last_count": 0,
                "duration_s": None, "error": None, "interval_s": INTERVAL_ALARMS_SECONDS,
            },
        }

    def get_status(self) -> dict:
        """Snapshot do estado das coletas (KPI/VIP/alarmes) para o indicador de sincronização."""
        return {
            "kpi": dict(self._status["kpi"]),
            "vip": dict(self._status["vip"]),
            "alarms": dict(self._status["alarms"]),
        }

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
            self._status[k].update({"state": "idle", "error": None})

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

    def _collect_kpis(self):
        if not self._collector:
            return
        self._status["kpi"]["state"] = "running"
        t0 = time.time()
        try:
            data = self._collector.collect_kpis()
            if data:
                db.insert_kpi_batch(data)
                self._evaluate_kpi_alerts(data)
                logger.debug(f"{len(data)} medições de KPI inseridas")
            self._status["kpi"].update({
                "state": "ok",
                "last_success": datetime.utcnow().isoformat(),
                "last_count": len(data) if data else 0,
                "duration_s": round(time.time() - t0, 2),
                "error": None,
            })
        except Exception as e:
            self._status["kpi"].update({
                "state": "error",
                "duration_s": round(time.time() - t0, 2),
                "error": str(e),
            })
            raise

    def _collect_vips(self, mode: str = None) -> int:
        if not self._collector:
            return 0
        # Serializa para evitar coleta concorrente (ciclo agendado vs. refresh manual).
        with self._vip_lock:
            if mode is None:
                now = time.time()
                if now - self._last_vip_full_time >= INTERVAL_VIP_FULL_SECONDS:
                    mode = "full"
                else:
                    mode = "express"

            self._status["vip"].update({"state": "running", "mode": mode})
            t0 = time.time()
            try:
                logger.info(f"Iniciando coleta de VIPs no modo: {mode}")
                data = self._collector.collect_vips(mode=mode)
                if data:
                    db.insert_vip_batch(data)
                    self._evaluate_vip_alerts(data)
                    logger.debug(f"{len(data)} medições de VIP inseridas (modo: {mode})")
                else:
                    logger.warning(f"Coleta de VIPs (modo: {mode}) retornou vazio — nenhuma medição inserida neste ciclo")

                if mode == "full":
                    self._last_vip_full_time = time.time()

                vips_total = len(getattr(self._collector, "vips_by_task", {}) or {})
                vips_with_data = len({m["vip_name"] for m in data}) if data else 0
                self._status["vip"].update({
                    "state": "ok",
                    "last_success": datetime.utcnow().isoformat(),
                    "last_count": len(data),
                    "duration_s": round(time.time() - t0, 2),
                    "error": None,
                    "vips_total": vips_total,
                    "vips_with_data": vips_with_data,
                })
                return len(data)
            except Exception as e:
                self._status["vip"].update({
                    "state": "error",
                    "duration_s": round(time.time() - t0, 2),
                    "error": str(e),
                })
                raise

    def collect_vips_now(self) -> int:
        """Coleta de VIPs sob demanda (botão de refresh do painel). Retorna nº de medições."""
        count = self._collect_vips(mode="express")
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
            self._status["alarms"]["state"] = "running"
            t0 = time.time()
            try:
                data = self._collector.collect_alarms()
                if data:
                    db.insert_alarms_batch(data)
                    logger.debug(f"{len(data)} alarmes inseridos")
                self._status["alarms"].update({
                    "state": "ok",
                    "last_success": datetime.utcnow().isoformat(),
                    "last_count": len(data) if data else 0,
                    "duration_s": round(time.time() - t0, 2),
                    "error": None,
                })
                return len(data) if data else 0
            except Exception as e:
                self._status["alarms"].update({
                    "state": "error",
                    "duration_s": round(time.time() - t0, 2),
                    "error": str(e),
                })
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
