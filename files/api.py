"""
Api: métodos Python expostos ao JavaScript via window.pywebview.api.
Todos os métodos retornam dicts/lists serializáveis para JSON.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from core import database as db
from core.scheduler import scheduler

logger = logging.getLogger(__name__)

_active_event: Optional[dict] = None
_update_callback = None  # função JS chamada quando novos dados chegam


class Api:

    # ── Ciclo de vida do evento ──────────────────────────────────────

    def load_event(self, json_path: str) -> dict:
        """Carrega arquivo de configuração do evento (.json)."""
        global _active_event
        try:
            path = Path(json_path)
            if not path.exists():
                return {"ok": False, "error": f"Arquivo não encontrado: {json_path}"}

            with open(path, encoding="utf-8") as f:
                config = json.load(f)

            db.save_event(config)
            _active_event = config

            return {"ok": True, "event": self._sanitize_event(config)}
        except Exception as e:
            logger.error(f"load_event error: {e}")
            return {"ok": False, "error": str(e)}

    def activate_event(self, event_id: str, mock: bool = False) -> dict:
        """Ativa evento e inicia gravação de dados."""
        global _active_event
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return {"ok": False, "error": "Evento não encontrado"}

            db.update_event_status(event_id, "ACTIVE")
            config["status"] = "ACTIVE"
            _active_event = config

            def _notify():
                if _update_callback:
                    try:
                        _update_callback()
                    except Exception:
                        pass

            scheduler.set_update_callback(_notify)
            scheduler.start(config, mock=mock)

            return {"ok": True, "event": self._sanitize_event(config)}
        except Exception as e:
            logger.error(f"activate_event error: {e}")
            return {"ok": False, "error": str(e)}

    def end_event(self, event_id: str) -> dict:
        """Encerra evento e para gravação."""
        try:
            scheduler.stop()
            db.update_event_status(event_id, "ENDED")
            global _active_event
            if _active_event and _active_event.get("id") == event_id:
                _active_event = None
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_events(self) -> list:
        """Lista todos os eventos salvos localmente."""
        events = db.get_events()
        return [self._sanitize_event(e) for e in events]

    def get_active_event(self) -> dict:
        """Retorna o evento atualmente ativo."""
        if _active_event:
            return {"ok": True, "event": self._sanitize_event(_active_event)}
        events = db.get_events(status="ACTIVE")
        if events:
            return {"ok": True, "event": self._sanitize_event(events[0])}
        return {"ok": False, "event": None}

    # ── Dados do mapa / sites ────────────────────────────────────────

    def get_sites(self, event_id: str) -> list:
        """Retorna sites com status atual para renderização no mapa."""
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return []

            latest = db.get_latest_kpi(event_id)
            util_by_site = self._aggregate_utilization(latest)

            sites_out = []
            thresholds = config.get("thresholds", {})
            warn = thresholds.get("utilization_warning", 80)
            crit = thresholds.get("utilization_critical", 95)

            for site in config.get("sites", []):
                util = util_by_site.get(site["id"])
                status = "unknown"
                if util is not None:
                    if util >= crit:
                        status = "critical"
                    elif util >= warn:
                        status = "warning"
                    else:
                        status = "healthy"

                sites_out.append({
                    "id":           site["id"],
                    "name":         site["name"],
                    "lat":          site["lat"],
                    "lng":          site["lng"],
                    "cells":        site.get("cells", []),
                    "status":       status,
                    "utilization":  round(util, 1) if util is not None else None,
                    "is_event_site": site.get("is_event_site", True),
                })

            return sites_out
        except Exception as e:
            logger.error(f"get_sites error: {e}")
            return []

    # ── KPI / gráfico ────────────────────────────────────────────────

    def get_kpi_series(self, event_id: str, site_id: str, metric: str, minutes: int = 60) -> dict:
        """Retorna série temporal para o gráfico de KPIs."""
        try:
            rows = db.get_kpi_series(event_id, site_id, metric, minutes)
            labels = [r["timestamp"] for r in rows]
            values = [r["value"] for r in rows]
            gaps = self._detect_gaps(labels, max_gap_seconds=90)

            config = db.get_event(event_id) or _active_event
            thresholds = config.get("thresholds", {}) if config else {}

            return {
                "ok":         True,
                "labels":     labels,
                "values":     values,
                "gaps":       gaps,
                "thresholds": {
                    "warning":  thresholds.get(f"{metric}_warning"),
                    "critical": thresholds.get(f"{metric}_critical"),
                },
            }
        except Exception as e:
            logger.error(f"get_kpi_series error: {e}")
            return {"ok": False, "labels": [], "values": [], "gaps": []}

    # ── VIPs ─────────────────────────────────────────────────────────

    def get_vips(self, event_id: str) -> list:
        """Retorna status atual de todos os VIPs."""
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return []

            vip_names = {v["name"] for v in config.get("vips", [])}
            latest = {r["vip_name"]: r for r in db.get_vip_latest(event_id)}
            thresholds = config.get("thresholds", {})
            rsrp_warn = thresholds.get("rsrp_warning", -100)
            rsrp_crit = thresholds.get("rsrp_critical", -110)

            out = []
            for vip in config.get("vips", []):
                name = vip["name"]
                row = latest.get(name)

                if row:
                    rsrp = row["rsrp"]
                    rsrq = row["rsrq"]
                    in_event = bool(row["in_event"])
                    serving = row["serving_cell"]

                    if rsrp is not None:
                        if rsrp <= rsrp_crit:
                            signal_status = "critical"
                        elif rsrp <= rsrp_warn:
                            signal_status = "warning"
                        else:
                            signal_status = "ok"
                    else:
                        signal_status = "unknown"
                else:
                    rsrp = rsrq = serving = None
                    in_event = False
                    signal_status = "unknown"

                out.append({
                    "name":         name,
                    "in_event":     in_event,
                    "serving_cell": serving,
                    "rsrp":         rsrp,
                    "rsrq":         rsrq,
                    "status":       signal_status,
                    "rsrp_min":     thresholds.get("rsrp_warning"),
                    "rsrp_max":     -40,  # teto prático
                })

            out.sort(key=lambda v: (not v["in_event"], v["status"] == "ok"))
            return out
        except Exception as e:
            logger.error(f"get_vips error: {e}")
            return []

    # ── Alertas ──────────────────────────────────────────────────────

    def get_alerts(self, event_id: str) -> list:
        try:
            return db.get_active_alerts(event_id)
        except Exception as e:
            logger.error(f"get_alerts error: {e}")
            return []

    def acknowledge_alert(self, alert_id: int) -> dict:
        try:
            db.acknowledge_alert(alert_id)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def silence_alert(self, alert_key: str) -> dict:
        try:
            db.silence_alert(alert_key)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Estado da aplicação ──────────────────────────────────────────

    def get_app_status(self) -> dict:
        return {
            "recording":  scheduler.is_recording,
            "db_size_mb": db.get_db_size_mb(),
            "now":        datetime.utcnow().isoformat(),
        }

    def open_file_dialog(self) -> dict:
        """Abre seletor de arquivo para carregar JSON de evento."""
        import webview
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Event Config (*.json)",)
        )
        if result:
            return {"ok": True, "path": result[0]}
        return {"ok": False, "path": None}

    # ── Helpers internos ─────────────────────────────────────────────

    def _sanitize_event(self, event: dict) -> dict:
        """Remove IMSIs antes de enviar para o frontend."""
        if isinstance(event, str):
            try:
                event = json.loads(event)
            except Exception:
                return {}

        out = dict(event)
        vips_safe = []
        for v in out.get("vips", []):
            vips_safe.append({"name": v.get("name", "")})
        out["vips"] = vips_safe

        # Remove config_json se vier do banco
        out.pop("config_json", None)
        return out

    def _aggregate_utilization(self, kpi_rows: list) -> dict:
        """Calcula utilização máxima por site a partir das células."""
        agg = {}
        for r in kpi_rows:
            if r["metric"] != "utilization":
                continue
            site = r["site_id"]
            if site not in agg or r["value"] > agg[site]:
                agg[site] = r["value"]
        return agg

    def _detect_gaps(self, timestamps: list, max_gap_seconds: int = 90) -> list:
        """Retorna lista de índices onde há gaps de coleta."""
        from datetime import datetime as dt
        gaps = []
        for i in range(1, len(timestamps)):
            try:
                t0 = dt.fromisoformat(timestamps[i - 1])
                t1 = dt.fromisoformat(timestamps[i])
                delta = (t1 - t0).total_seconds()
                if delta > max_gap_seconds:
                    gaps.append({"from_idx": i - 1, "to_idx": i, "seconds": delta})
            except Exception:
                pass
        return gaps
