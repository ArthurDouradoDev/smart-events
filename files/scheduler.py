"""
Scheduler: executa a coleta de dados em background a cada N segundos.
Roda em thread separada para não bloquear a interface.
"""

import logging
import threading
from typing import Callable, Optional

from core import database as db
from core.collector import BaseCollector, build_collector

logger = logging.getLogger(__name__)

INTERVAL_KPI_SECONDS = 60   # OSS: granularidade de 1 minuto
INTERVAL_VIP_SECONDS = 15   # Trace: mais frequente para VIPs


class Scheduler:
    def __init__(self):
        self._collector: Optional[BaseCollector] = None
        self._threads: list = []
        self._stop_event = threading.Event()
        self._on_update: Optional[Callable] = None  # callback para notificar JS
        self._event_config: Optional[dict] = None
        self._recording: bool = False

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

        t_kpi = threading.Thread(
            target=self._loop, args=(self._collect_kpis, INTERVAL_KPI_SECONDS),
            daemon=True, name="kpi-collector"
        )
        t_vip = threading.Thread(
            target=self._loop, args=(self._collect_vips, INTERVAL_VIP_SECONDS),
            daemon=True, name="vip-collector"
        )
        self._threads = [t_kpi, t_vip]
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

    def _loop(self, fn: Callable, interval: int):
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
        data = self._collector.collect_kpis()
        if data:
            db.insert_kpi_batch(data)
            self._evaluate_kpi_alerts(data)
            logger.debug(f"{len(data)} medições de KPI inseridas")

    def _collect_vips(self):
        if not self._collector:
            return
        data = self._collector.collect_vips()
        if data:
            db.insert_vip_batch(data)
            self._evaluate_vip_alerts(data)
            logger.debug(f"{len(data)} medições de VIP inseridas")

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

            if metric in ("utilization", "utilization_dl", "utilization_ul"):
                metric_label = ""
                if metric == "utilization_dl":
                    metric_label = " DL"
                elif metric == "utilization_ul":
                    metric_label = " UL"

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
            elif metric == "availability":
                if val < avail_crit:
                    key = f"avail_crit_{cell_id}"
                    if not db.is_silenced(key) and key not in seen:
                        db.insert_alert({
                            "event_id":  self._event_config["id"],
                            "level":     "EVENT",
                            "severity":  "CRITICAL",
                            "site_id":   site_id,
                            "cell_id":   cell_id,
                            "message":   f"Disponibilidade crítica: {val:.1f}% na célula {cell_id}",
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
