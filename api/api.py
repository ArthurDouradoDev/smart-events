"""
Api: métodos Python expostos ao JavaScript via window.pywebview.api.
Todos os métodos retornam dicts/lists serializáveis para JSON.
"""

import json
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from core import database as db
from core import credentials
from core.scheduler import scheduler
from core.log_buffer import log_buffer
from core.kpi_formulas import catalog_for_api

logger = logging.getLogger(__name__)

_active_event: Optional[dict] = None
_update_callback = None  # função JS chamada quando novos dados chegam
_activation_lock = threading.RLock()


class Api:

    # URL do servidor FastAPI local embutido (injetada por main.py no startup).
    _server_url: str = ""

    def __init__(self):
        # Uma leitura transitoriamente bloqueada nunca deve ser traduzida para
        # "o evento não tem sites", pois o frontend removeria todos do mapa.
        self._sites_cache: dict[str, list] = {}

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
            # Exporta para o servidor de API central para sincronizar com os outros computadores
            db.export_event_to_server(config)
            _active_event = config

            return {"ok": True, "event": self._sanitize_event(config)}
        except Exception as e:
            logger.error(f"load_event error: {e}")
            return {"ok": False, "error": str(e)}

    def activate_event(self, event_id: str, mock: bool = False, cliente: str = None) -> dict:
        """Ativa evento e inicia gravação de dados.

        `cliente` (opcional): fixa o cliente do evento quando ele não tinha um (evento legado),
        persistindo `oss.cliente` para que as credenciais e a renovação de sessão funcionem.
        """
        global _active_event
        try:
            config = db.get_event(event_id)
            if config is None and _active_event and _active_event.get("id") == event_id:
                config = _active_event
            if not config:
                return {"ok": False, "error": "Evento não encontrado"}

            oss = config.get("oss", {}) or {}

            # Evento legado sem cliente: o operador escolheu um no modal — fixa e persiste.
            if cliente:
                oss["cliente"] = cliente
                config["oss"] = oss
                db.save_event(config)
                try:
                    db.export_event_to_server(config)
                except Exception as ex:
                    logger.warning(f"activate_event: falha ao exportar cliente do evento: {ex}")

            # 1º acesso: a coleta HTTP precisa de credenciais (cliente, regional). Faltando,
            # devolve needs_credentials para o front abrir o modal e re-chamar activate_event.
            import_folder = oss.get("import_folder", "")
            is_http = not mock and not (import_folder and Path(import_folder).exists())
            if is_http:
                cliente = (oss.get("cliente") or "").strip()
                region = (oss.get("region") or "").strip().upper()
                if not credentials.has_credentials(cliente, region):
                    try:
                        base_url = credentials.resolve_base_url(oss)
                    except ValueError:
                        # Evento legado ainda precisa do modal para o operador escolher
                        # o cliente. Nenhum host é presumido enquanto essa identidade falta.
                        base_url = ""
                    return {
                        "ok": False,
                        "needs_credentials": True,
                        "cliente": cliente,
                        "region": region,
                        "base_url": base_url,
                    }

            with _activation_lock:
                # A geração anterior é invalidada antes de qualquer mudança no banco.
                # Workers que excederem o join podem terminar a requisição, mas seu
                # contexto já não terá autorização para persistir ou atualizar status.
                scheduler.stop()
                # Several events may be operational at the same time (for
                # example, Santo Amaro and Curitiba). The scheduler remains
                # exclusive through scheduler.stop()/start(), but selecting
                # one event locally must not mark the others as historical.
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
                try:
                    scheduler.start(config, mock=mock)
                except Exception:
                    db.update_event_status(event_id, "ENDED")
                    _active_event = None
                    raise

            # Sincroniza os VIPs do cliente/OSS deste evento após ativá-lo
            try:
                oss = config.get("oss", {}).get("region")
                cliente = config.get("oss", {}).get("cliente")
                db.sync_vips_from_server(oss=oss, cliente=cliente)
            except Exception as se:
                logger.error(f"Erro ao sincronizar VIPs ao ativar evento: {se}")

            return {"ok": True, "event": self._sanitize_event(config)}
        except Exception as e:
            logger.error(f"activate_event error: {e}")
            return {"ok": False, "error": str(e)}

    def end_event(self, event_id: str) -> dict:
        """Encerra evento e para gravação."""
        try:
            with _activation_lock:
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
        out = []
        for e in events:
            full_event = db.get_event(e["id"])
            if full_event:
                out.append(self._sanitize_event(full_event))
            else:
                out.append(self._sanitize_event(e))
        return out

    def sync_events(self) -> dict:
        """
        Sincroniza eventos primeiro (garante que events existam para FK de event_vips)
        e depois VIPs com suas associações a eventos.
        """
        try:
            event_stats = db.sync_events_from_server()
            db.sync_clientes_from_server()  # atualiza o catálogo de clientes/regionais/IPs

            # Detecta cliente/OSS do evento ativo para filtrar a sincronização de VIPs
            oss = None
            cliente = None
            global _active_event
            src = None
            if _active_event and isinstance(_active_event.get("oss"), dict):
                src = _active_event["oss"]
            else:
                active_events = db.get_events(status="ACTIVE")
                if active_events:
                    full_event = db.get_event(active_events[0]["id"])
                    if full_event and isinstance(full_event.get("oss"), dict):
                        src = full_event["oss"]
            if src:
                oss = src.get("region")
                cliente = src.get("cliente")

            vip_stats = db.sync_vips_from_server(oss=oss, cliente=cliente)
            return {"ok": True, "stats": {"vips": vip_stats, "events": event_stats}}
        except Exception as e:
            logger.error(f"sync_events error: {e}")
            return {"ok": False, "error": str(e)}

    def get_settings(self) -> dict:
        """Retorna as configurações atuais do aplicativo."""
        try:
            settings = db.get_settings()
            return {"ok": True, "settings": settings}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_settings(self, settings: dict) -> dict:
        """Salva novas configurações do aplicativo e força uma sincronização com o servidor."""
        try:
            db.save_settings(settings)
            stats = db.sync_events_from_server()
            return {"ok": True, "stats": stats}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Credenciais (Cliente → Regional) ─────────────────────────────

    def get_clientes(self) -> dict:
        """Catálogo Cliente → {name, logo, logo_url, regionais:[...]} para popular a UI."""
        try:
            summary = credentials.clientes_summary()
            server = (self._server_url or db.get_settings().get("server_url", "")).rstrip("/")
            for c in summary.values():
                c["logo_url"] = f"{server}/logos/{c['logo']}" if (c.get("logo") and server) else ""
            return {"ok": True, "clientes": summary}
        except Exception as e:
            logger.error(f"get_clientes error: {e}")
            return {"ok": False, "error": str(e)}

    def sync_clientes(self) -> dict:
        """Sincroniza o catálogo de clientes/regionais a partir do servidor central."""
        try:
            return {"ok": True, **db.sync_clientes_from_server()}
        except Exception as e:
            logger.error(f"sync_clientes error: {e}")
            return {"ok": False, "error": str(e)}

    def get_credentials_status(self, cliente: str) -> dict:
        """Status das credenciais de um cliente, SEM expor senhas (só username + flags)."""
        try:
            return {"ok": True, **credentials.status_for(cliente)}
        except Exception as e:
            logger.error(f"get_credentials_status error: {e}")
            return {"ok": False, "error": str(e)}

    def save_credentials(self, cliente: str, region: str, username: str, password: str) -> dict:
        """Salva a credencial de um par (cliente, regional)."""
        try:
            if not (cliente and region and username and password):
                return {"ok": False, "error": "Cliente, regional, usuário e senha são obrigatórios."}
            credentials.save_credential(cliente, region, username, password)
            return {"ok": True}
        except Exception as e:
            logger.error(f"save_credentials error: {e}")
            return {"ok": False, "error": str(e)}

    def save_shared_credentials(self, cliente: str, username: str, password: str) -> dict:
        """Salva a credencial compartilhada do cliente (vale para todas as regionais dele)."""
        try:
            if not (cliente and username and password):
                return {"ok": False, "error": "Cliente, usuário e senha são obrigatórios."}
            credentials.save_shared(cliente, username, password)
            return {"ok": True}
        except Exception as e:
            logger.error(f"save_shared_credentials error: {e}")
            return {"ok": False, "error": str(e)}

    def delete_credentials(self, cliente: str, region: str) -> dict:
        """Remove o override de uma regional (volta a herdar a compartilhada do cliente)."""
        try:
            credentials.delete_credential(cliente, region)
            return {"ok": True}
        except Exception as e:
            logger.error(f"delete_credentials error: {e}")
            return {"ok": False, "error": str(e)}

    def get_server_url(self) -> dict:
        """Retorna a URL do servidor local embutido."""
        url = self._server_url or db.get_settings().get("server_url", "")
        return {"ok": True, "url": url}

    def open_server_ui(self, path: str = "/") -> dict:
        """Abre a página de edição do servidor local no navegador padrão."""
        try:
            import webbrowser
            base = (self._server_url or db.get_settings().get("server_url", "")).rstrip("/")
            if not base:
                return {"ok": False, "error": "Servidor local indisponível"}
            webbrowser.open(f"{base}{path}")
            return {"ok": True, "url": f"{base}{path}"}
        except Exception as e:
            logger.error(f"open_server_ui error: {e}")
            return {"ok": False, "error": str(e)}

    def get_active_event(self) -> dict:
        """Retorna o evento atualmente ativo."""
        global _active_event
        if _active_event:
            return {"ok": True, "event": self._sanitize_event(_active_event)}
        events = db.get_events(status="ACTIVE")
        if events:
            full_event = db.get_event(events[0]["id"])
            if full_event:
                _active_event = full_event
                return {"ok": True, "event": self._sanitize_event(full_event)}
        return {"ok": False, "event": None}

    def check_vpn(self) -> dict:
        """Verifica a conexão com a VPN pingando o IP do OSS do evento ativo.

        Resolve o alvo na mesma ordem de precedência de build_collector()
        (oss.base_url → mapa regional exato) e confirma a conectividade
        pela presença de "TTL=" no retorno do ping nativo do Windows.
        """
        try:
            global _active_event
            if not _active_event:
                # During bootstrap the UI can be ready before the active event is
                # restored. Missing context is not a disconnected VPN.
                return {
                    "ok": True,
                    "connected": None,
                    "target": None,
                    "reason": "no_active_event",
                }

            oss = (_active_event or {}).get("oss", {}) if _active_event else {}

            base_url = credentials.resolve_base_url(oss)
            target = urlparse(base_url).hostname
            if not target:
                raise ValueError("base_url do OSS não contém um host válido")

            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(
                ["ping", "-n", "2", "-w", "2500", target],
                capture_output=True,
                timeout=15,
                creationflags=flags,
            )
            # Exit status is independent from the Windows display language.
            connected = result.returncode == 0
            return {"ok": True, "connected": connected, "target": target}
        except Exception as e:
            logger.error(f"check_vpn error: {e}")
            return {"ok": False, "error": str(e)}

    def reauth_session(self) -> dict:
        """Compatibilidade: retoma imediatamente a renovação headless.

        O endpoint antigo abria um navegador visível. Bloqueios de sessão são agora
        tratados automaticamente com backoff, sem intervenção do operador.
        """
        global _active_event
        try:
            oss = (_active_event or {}).get("oss", {}) if _active_event else {}
            base_url = credentials.resolve_base_url(oss).rstrip("/")

            # Resolve o session.json da regional e limpa qualquer estado de espera
            # legado para que o próximo ciclo tente novamente em modo headless.
            from core.collector import HttpCollector
            session_file = HttpCollector._resolve_session_file(base_url)
            HttpCollector.reset_interactive_state(base_url=base_url)
            coll = scheduler._collector
            if hasattr(coll, "_invalidate_session"):
                coll._invalidate_session("monitoring")
                coll._invalidate_session("trace")
            logger.info("reauth_session: renovação headless liberada para nova tentativa.")
            return {
                "ok": True,
                "automatic": True,
                "base_url": base_url,
                "session_file": str(session_file),
            }
        except Exception as e:
            logger.error(f"reauth_session error: {e}")
            return {"ok": False, "error": str(e)}

    # ── Dados do mapa / sites ────────────────────────────────────────

    def get_sites(self, event_id: str, timestamp: Optional[str] = None,
                  metric: str = "utilization_dl") -> list:
        """Retorna sites com status atual para renderização no mapa."""
        config = (_active_event if _active_event and _active_event.get("id") == event_id else None)
        try:
            config = db.get_event(event_id) or config
            if not config:
                return []

            latest = db.get_latest_kpi(event_id, timestamp)
            util_by_site = self._aggregate_utilization(latest)

            # Calcula valor contextual para a métrica selecionada (exibição na lista)
            VOLUME_METRICS = {"user_count", "traffic_volume_dl", "traffic_volume_ul"}

            if metric != "utilization_dl":
                latest_metric = db.get_latest_kpi_by_metric(event_id, metric, timestamp)
            else:
                latest_metric = latest  # reusa os dados já carregados

            metric_by_site = self._aggregate_metric_for_list(latest_metric, metric)
            # Dados novos possuem uma linha SITE; ela é a fonte de verdade e
            # substitui qualquer regra antiga de agregação da lista.
            persisted_metric = db.get_latest_site_kpi_by_metric(event_id, metric, timestamp)
            for row in persisted_metric:
                metric_by_site[row["site_id"]] = row["value"]

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
                    "id":              site["id"],
                    "name":            site["name"],
                    "lat":             site["lat"],
                    "lng":             site["lng"],
                    "cells":           site.get("cells", []),
                    "status":          status,
                    "utilization":     round(util, 1) if util is not None else None,
                    "metric_value":    metric_by_site.get(site["id"]),
                    "metric_is_share": False,
                    "is_event_site":   site.get("is_event_site", True),
                })

            self._sites_cache[event_id] = sites_out
            return sites_out
        except Exception as e:
            logger.error(f"get_sites error: {e}")
            cached = self._sites_cache.get(event_id)
            if cached is not None:
                return cached
            if config:
                fallback = [{
                    "id": site["id"], "name": site["name"],
                    "lat": site["lat"], "lng": site["lng"],
                    "cells": site.get("cells", []), "status": "unknown",
                    "utilization": None, "metric_value": None,
                    "metric_is_share": False,
                    "is_event_site": site.get("is_event_site", True),
                } for site in config.get("sites", [])]
                self._sites_cache[event_id] = fallback
                return fallback
            return []

    def get_site_cells(self, event_id: str, site_id: str) -> list:
        """
        Retorna a lista de células de um site específico.
        Usado para popular o seletor de célula no gráfico.
        """
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return []
            for site in config.get("sites", []):
                if site["id"] == site_id:
                    cells = site.get("cells", [])
                    out = []
                    for c in cells:
                        if isinstance(c, str):
                            out.append({"id": c, "label": c})
                        else:
                            out.append({
                                "id":    c.get("id", ""),
                                "label": c.get("id", ""),
                                "tech":  c.get("tech"),
                                "freq":  c.get("frequency"),
                            })
                    return out
            return []
        except Exception as e:
            logger.error(f"get_site_cells error: {e}")
            return []

    # ── KPI / gráfico ────────────────────────────────────────────────

    @staticmethod
    def _configured_kpi_technologies(config: dict) -> list[str]:
        """Famílias realmente consultadas pelas tasks PM do evento.

        O evento legado com ``pm_task_id`` é 4G por contrato. Quando ``pm_tasks``
        existir, a tecnologia declarada na task é a fonte de verdade; nomes de
        células não são usados para inferência (Curitiba não carrega 4G no nome).
        """
        integration = (config or {}).get("integration", {}) or {}
        configured = integration.get("pm_tasks") or []
        if isinstance(configured, dict):
            configured = [{"tech": tech, "task_id": task_id}
                          for tech, task_id in configured.items()]
        technologies = []
        for item in configured:
            if not isinstance(item, dict) or item.get("task_id") in (None, ""):
                continue
            tech = str(item.get("tech") or "").upper().replace("-", "_").replace(" ", "_")
            family = "4G" if tech in {"4G", "LTE"} else (
                "5G" if tech in {"5G", "NR", "NRCELL", "NR_CELL",
                                  "5G_NRCELL", "NRDUCELL", "NR_DU_CELL",
                                  "5G_NRDUCELL"} else None)
            if family and family not in technologies:
                technologies.append(family)
        if not technologies and integration.get("pm_task_id") not in (None, ""):
            technologies.append("4G")
        return technologies

    def get_kpi_catalog(self, event_id: str = None) -> dict:
        """Metadados do seletor limitados às tasks PM do evento solicitado."""
        global _active_event
        config = None
        if event_id:
            config = db.get_event(event_id)
        elif _active_event:
            config = _active_event
        catalog = catalog_for_api()
        if config is None:
            # O bootstrap pede o catálogo antes de restaurar o evento. A segunda
            # chamada, disparada por change:activeEvent, aplicará o filtro real.
            return {"ok": True, "metrics": catalog, "technologies": []}
        technologies = self._configured_kpi_technologies(config)
        metrics = [item for item in catalog if item.get("technology") in technologies]
        return {"ok": True, "metrics": metrics, "technologies": technologies}

    def get_kpi_series(self, event_id: str, site_id: str, metric: str,
                       minutes: int = 60, cell_id: str = "__all__", technology: str = None) -> dict:
        """Retorna série temporal para o gráfico de KPIs."""
        try:
            if cell_id == "__all__":
                site_rows = db.get_kpi_site_series(event_id, site_id, metric, minutes, technology)
                if site_rows:
                    labels = [row["timestamp"] for row in site_rows]
                    # O agregado persistido não substitui as linhas por célula do
                    # gráfico "Site completo" — só a série somada/recalculada.
                    cell_rows = db.get_kpi_series(event_id, site_id, metric, minutes)
                    cell_ids = sorted({r["cell_id"] for r in cell_rows if r.get("cell_id")})
                    cell_ts_vals = {(r["cell_id"], r["timestamp"]): r["value"]
                                     for r in cell_rows if r.get("cell_id") and r.get("timestamp")}
                    cells_data = {cid: [cell_ts_vals.get((cid, ts)) for ts in labels] for cid in cell_ids}
                    return {
                        "ok": True, "labels": labels, "values": [row["value"] for row in site_rows],
                        "cells_data": cells_data, "gaps": self._detect_gaps(labels, max_gap_seconds=90),
                        "technology": technology, "persisted_site_aggregate": True,
                        "thresholds": self._metric_thresholds(event_id, metric),
                    }
            # Compatibilidade com históricos pré-Fase 2; novas linhas de Site
            # completo retornam acima e não são agregadas por médias na API.
            # 1. Obter medições brutas
            if metric == "utilization":
                rows = db.get_kpi_series(event_id, site_id, "utilization", minutes)
                if not rows:
                    # Busca DL e UL e combina por cell/timestamp
                    rows_dl = db.get_kpi_series(event_id, site_id, "utilization_dl", minutes)
                    rows_ul = db.get_kpi_series(event_id, site_id, "utilization_ul", minutes)
                    
                    combined = {}
                    for r in rows_dl:
                        key = (r["cell_id"], r["timestamp"])
                        combined[key] = r["value"]
                    for r in rows_ul:
                        key = (r["cell_id"], r["timestamp"])
                        if key in combined:
                            combined[key] = max(combined[key], r["value"])
                        else:
                            combined[key] = r["value"]
                    
                    rows = [
                        {"cell_id": cell, "timestamp": ts, "value": val}
                        for (cell, ts), val in combined.items()
                    ]
            else:
                rows = db.get_kpi_series(event_id, site_id, metric, minutes)

            # Se célula específica solicitada, filtrar antes de agregar
            if cell_id and cell_id not in ("__all__", "__media__"):
                rows = [r for r in rows if r.get("cell_id") == cell_id]

            # 2. Agrupar por timestamp para consolidar dados de múltiplas células do mesmo site
            ts_groups = {}
            for r in rows:
                ts = r["timestamp"]
                val = r["value"]
                if val is not None:
                    if ts not in ts_groups:
                        ts_groups[ts] = []
                    ts_groups[ts].append(val)

            # 3. Consolidar grupos de timestamps para ter um único valor por timestamp no gráfico
            aggregated = []
            for ts, vals in sorted(ts_groups.items()):
                if not vals:
                    continue
                # Se o usuário escolheu "Média" explicitamente, sempre calcula AVG
                if cell_id == "__media__":
                    val = sum(vals) / len(vals)
                elif "availability" in metric or "accessibility" in metric:
                    val = sum(vals) / len(vals)
                elif "throughput" in metric:
                    val = sum(vals)          # throughput é somado (capacidade do site)
                elif "rsrp" in metric or "rsrq" in metric:
                    val = sum(vals) / len(vals)
                else:
                    val = max(vals)          # utilização, user_count → pior/máximo
                aggregated.append({"timestamp": ts, "value": val})

            labels = [r["timestamp"] for r in aggregated]
            values = [r["value"] for r in aggregated]
            gaps = self._detect_gaps(labels, max_gap_seconds=90)

            cells_data = {}
            if cell_id == "__all__":
                # Obter todas as cell_ids únicas presentes
                cell_ids = sorted(list({r["cell_id"] for r in rows if r.get("cell_id")}))
                # Mapear (cell_id, timestamp) -> value
                cell_ts_vals = {}
                for r in rows:
                    if r.get("cell_id") and r.get("timestamp"):
                        cell_ts_vals[(r["cell_id"], r["timestamp"])] = r["value"]
                # Alinhar valores de cada célula com os timestamps ordenados em labels
                for cid in cell_ids:
                    cells_data[cid] = [cell_ts_vals.get((cid, ts)) for ts in labels]

            config = db.get_event(event_id) or _active_event
            thresholds = config.get("thresholds", {}) if config else {}

            warning_th = thresholds.get(f"{metric}_warning")
            critical_th = thresholds.get(f"{metric}_critical")
            if warning_th is None and "utilization" in metric:
                warning_th = thresholds.get("utilization_warning")
            if critical_th is None and "utilization" in metric:
                critical_th = thresholds.get("utilization_critical")

            return {
                "ok":         True,
                "labels":     labels,
                "values":     values,
                "cells_data": cells_data,
                "gaps":       gaps,
                "thresholds": {
                    "warning":  warning_th,
                    "critical": critical_th,
                },
            }
        except Exception as e:
            logger.error(f"get_kpi_series error: {e}")
            return {"ok": False, "labels": [], "values": [], "gaps": []}

    def _metric_thresholds(self, event_id: str, metric: str) -> dict:
        config = db.get_event(event_id) or _active_event or {}
        thresholds = config.get("thresholds", {})
        return {
            "warning": thresholds.get(f"{metric}_warning", thresholds.get("utilization_warning") if "utilization" in metric else None),
            "critical": thresholds.get(f"{metric}_critical", thresholds.get("utilization_critical") if "utilization" in metric else None),
        }

    # ── VIPs (cadastro global) ───────────────────────────────────────

    def list_vips(self) -> list:
        """Lista todos os VIPs cadastrados globalmente."""
        try:
            return db.get_vips()
        except Exception as e:
            logger.error(f"list_vips error: {e}")
            return []

    def create_vip(self, name: str, role: Optional[str] = None,
                    notes: Optional[str] = None) -> dict:
        """Cadastra um VIP global. Id é gerado a partir do nome (slug)."""
        try:
            vip = db.save_vip({"name": name, "role": role, "notes": notes})
            db.export_vip_to_server(vip)
            return {"ok": True, "vip": vip}
        except Exception as e:
            logger.error(f"create_vip error: {e}")
            return {"ok": False, "error": str(e)}

    def update_vip(self, vip_id: str, name: str,
                    role: Optional[str] = None,
                    notes: Optional[str] = None) -> dict:
        """Atualiza um VIP existente (mantendo o id)."""
        try:
            vip = db.save_vip({"id": vip_id, "name": name, "role": role, "notes": notes})
            db.export_vip_to_server(vip)
            return {"ok": True, "vip": vip}
        except Exception as e:
            logger.error(f"update_vip error: {e}")
            return {"ok": False, "error": str(e)}

    def delete_vip(self, vip_id: str) -> dict:
        """Remove um VIP global (em cascata, sai de event_vips)."""
        try:
            removed = db.delete_vip(vip_id)
            db.delete_vip_on_server(vip_id)
            return {"ok": removed}
        except Exception as e:
            logger.error(f"delete_vip error: {e}")
            return {"ok": False, "error": str(e)}

    def get_event_vips(self, event_id: str) -> list:
        """Lista os VIPs atribuídos ao evento (com name/role/notes + task_id)."""
        try:
            return db.get_event_vips(event_id)
        except Exception as e:
            logger.error(f"get_event_vips error: {e}")
            return []

    def assign_vip_to_event(self, event_id: str, vip_id: str,
                             task_id: Optional[int] = None) -> dict:
        """Associa um VIP global a um evento e grava o task_id desse evento."""
        try:
            db.assign_vip_to_event(event_id, vip_id, task_id)
            # Reflete a mudança no servidor: re-exporta o evento atualizado.
            evt = db.get_event(event_id)
            if evt:
                db.export_event_to_server(evt)
            return {"ok": True}
        except Exception as e:
            logger.error(f"assign_vip_to_event error: {e}")
            return {"ok": False, "error": str(e)}

    def unassign_vip_from_event(self, event_id: str, vip_id: str) -> dict:
        """Remove a associação VIP↔evento (não apaga o VIP global)."""
        try:
            db.unassign_vip_from_event(event_id, vip_id)
            evt = db.get_event(event_id)
            if evt:
                db.export_event_to_server(evt)
            return {"ok": True}
        except Exception as e:
            logger.error(f"unassign_vip_from_event error: {e}")
            return {"ok": False, "error": str(e)}

    # ── VIPs (medições em tempo real do evento ativo) ────────────────

    def get_vips(self, event_id: str, timestamp: Optional[str] = None) -> list:
        """Retorna status atual de todos os VIPs (opcionalmente até timestamp).

        VIP measurements são globais: a medição mais recente é a mesma
        independente do evento que está sendo visualizado. O campo `in_event`
        é calculado dinamicamente comparando a célula servidora com os sites
        do evento atual — sem depender do valor gravado no banco.
        """
        try:
            # Migra dados legados do banco específico deste evento para o global
            # (executa apenas uma vez por evento por sessão).
            db.migrate_event_vip_measurements(event_id)

            config = db.get_event(event_id) or _active_event
            if not config:
                return []

            # Lista vem do JOIN event_vips↔vips (cadastro global).
            event_vips = db.get_event_vips(event_id)
            # get_vip_latest agora é global: sem filtro de event_id.
            latest = {r["vip_name"]: r for r in db.get_vip_latest(timestamp)}
            thresholds = config.get("thresholds", {})
            rsrp_warn = thresholds.get("rsrp_warning", -100)
            rsrp_crit = thresholds.get("rsrp_critical", -110)

            resolve_site_id = self._create_cell_resolver(config.get("sites", []))

            site_id_to_name = {site["id"]: site["name"] for site in config.get("sites", [])}

            out = []
            for vip in event_vips:
                name = vip["name"]
                row = latest.get(name)

                if row:
                    rsrp    = row["rsrp"]
                    rsrq    = row["rsrq"]
                    serving = row["serving_cell"]
                    last_ts = row["timestamp"]

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
                    rsrp = rsrq = serving = last_ts = None
                    signal_status = "unknown"

                # in_event calculado dinamicamente contra os sites do evento atual —
                # não depende do valor gravado, por isso é correto ao trocar de evento.
                serving_site = resolve_site_id(serving)
                in_event = bool(serving_site)
                serving_site_name = (
                    site_id_to_name.get(serving_site)
                    if serving_site
                    else self._infer_site_label_from_cell(serving)
                )

                out.append({
                    "id":                vip["id"],
                    "name":              name,
                    "role":              vip.get("role"),
                    "notes":             vip.get("notes"),
                    "task_id":           vip.get("task_id"),
                    "in_event":          in_event,
                    "serving_cell":      serving,
                    "serving_site":      serving_site,
                    "serving_site_name": serving_site_name,
                    "last_timestamp":    last_ts,
                    "rsrp":              rsrp,
                    "rsrq":              rsrq,
                    "status":            signal_status,
                    "rsrp_min":          thresholds.get("rsrp_warning"),
                    "rsrp_max":          -40,  # teto prático
                })

            out.sort(key=lambda v: (not v["in_event"], v["status"] == "ok"))
            return out
        except Exception as e:
            logger.error(f"get_vips error: {e}")
            return []

    def refresh_vips(self, event_id: str) -> dict:
        """Força uma coleta imediata de VIPs (botão de refresh do painel VIP).

        Retorna o número de medições inseridas. Requer coleta ativa.
        """
        try:
            if not scheduler.is_recording:
                return {"ok": False, "error": "Coleta não está ativa para este evento."}
            count = scheduler.collect_vips_now()
            return {"ok": True, "count": count}
        except Exception as e:
            logger.error(f"refresh_vips error: {e}")
            return {"ok": False, "error": str(e)}

    def get_vip_series(self, event_id: str, vip_name: str, minutes: int = 60) -> dict:
        """Retorna série temporal de RSRP/RSRQ para um VIP específico."""
        try:
            config = db.get_event(event_id) or _active_event
            sites = config.get("sites", []) if config else []
            resolve_site_id = self._create_cell_resolver(sites)
            site_id_to_name = {site["id"]: site["name"] for site in sites}

            rows = db.get_vip_series(event_id, vip_name, minutes)
            for row in rows:
                cell = row.get("serving_cell")
                site_id = resolve_site_id(cell)
                row["serving_site"] = site_id
                row["serving_site_name"] = (
                    site_id_to_name.get(site_id)
                    if site_id
                    else self._infer_site_label_from_cell(cell)
                )

            return {"ok": True, "series": rows}
        except Exception as e:
            logger.error(f"get_vip_series error: {e}")
            return {"ok": False, "series": []}

    # ── Alarmes (iMaster FM website, filtrados por tipo) ─────────────

    def get_alarms(self, event_id: str, timestamp: Optional[str] = None) -> list:
        """Retorna os alarmes correntes (opcionalmente até timestamp), marcados com
        `in_event`/`serving_site` pela correlação do `source` (meName) com os sites
        do evento — feita aqui (query time) para refletir os sites do evento atual,
        como os VIPs. A coleta continua sendo da rede toda (não recortada)."""
        try:
            rows = db.get_alarms(event_id, timestamp)
            config = db.get_event(event_id) or _active_event or {}
            sites = config.get("sites", [])
            for r in rows:
                site_id, site_name = self._resolve_site_for_source(sites, r.get("source"))
                r["serving_site"] = site_id
                r["serving_site_name"] = site_name
                r["in_event"] = bool(site_id)
            return rows
        except Exception as e:
            logger.error(f"get_alarms error: {e}")
            return []

    @staticmethod
    def _create_cell_resolver(sites: list):
        cell_to_site = {}
        for site in sites:
            site_id = site["id"]
            for cell in site.get("cells", []):
                if isinstance(cell, str):
                    cell_to_site[cell.upper()] = site_id
                elif isinstance(cell, dict):
                    c_id = cell.get("id")
                    if c_id:
                        cell_to_site[c_id.upper()] = site_id
                    obj_no = cell.get("obj_no")
                    if obj_no is not None:
                        cell_to_site[str(obj_no).upper()] = site_id

        def resolve_site_id(cell_id):
            if not cell_id:
                return None
            cell_id_str = str(cell_id).strip()
            cell_id_upper = cell_id_str.upper()

            # 1. Match exato com célula/obj_no mapeado
            if cell_id_upper in cell_to_site:
                return cell_to_site[cell_id_upper]

            # 2. Match por prefixo ou contendo no site ID/Nome
            for site in sites:
                s_id = site["id"]
                s_id_upper = s_id.upper()
                s_name_upper = site.get("name", "").upper()
                id_matches = (
                    cell_id_upper.startswith(s_id_upper)
                    or (len(s_id_upper) >= 4 and s_id_upper in cell_id_upper)
                )
                name_matches = len(s_name_upper) >= 4 and (
                    s_name_upper in cell_id_upper
                    or (len(cell_id_upper) >= 4 and cell_id_upper in s_name_upper)
                )
                if id_matches or name_matches:
                    return s_id

            # 3. Decodificação de ID global de célula (4G ECI // 256 ou 5G NCI // 4096)
            if cell_id_str.isdigit():
                try:
                    val = int(cell_id_str)
                    for divisor in (256, 4096):
                        inferred_site = val // divisor
                        inferred_str = str(inferred_site)
                        if inferred_site > 0:
                            for site in sites:
                                s_id = site["id"]
                                s_id_upper = s_id.upper()
                                s_name_upper = site.get("name", "").upper()
                                if inferred_str in s_id_upper or inferred_str in s_name_upper:
                                    return s_id
                except ValueError:
                    pass

            return None
        return resolve_site_id

    @staticmethod
    def _infer_site_label_from_cell(cell_id):
        """Extract an external site label from standard ``SITE_SECTOR`` IDs.

        The inferred label is presentation context only. It is deliberately not
        returned by ``_create_cell_resolver`` because an inferred external site
        must not make a VIP count as being inside the current event.
        """
        if not cell_id:
            return None
        value = str(cell_id).strip()
        if "_" not in value:
            return None
        site_label, sector = value.rsplit("_", 1)
        if site_label and sector.isdigit():
            return site_label
        return None

    @staticmethod
    def _resolve_site_for_source(sites: list, source) -> tuple:
        """Casa o `source` do alarme (meName, ex.: SR-UWCTJ1) com um site do evento.
        Match por igualdade/prefixo/substring contra o id e o nome do site."""
        if not source:
            return None, None
        src = str(source).strip().upper()
        if not src:
            return None, None
        for site in sites:
            s_id = site.get("id", "")
            s_id_u = s_id.upper()
            s_name_u = (site.get("name") or "").upper()
            if s_id_u and (src == s_id_u or src.startswith(s_id_u) or s_id_u in src):
                return s_id, site.get("name")
            if s_name_u and (s_name_u in src or src in s_name_u):
                return s_id, site.get("name")
        return None, None

    def get_alarm_catalog(self) -> dict:
        """Nomes de alarme disponíveis (ordenados) para o multi-select do filtro."""
        try:
            from core.collector import _load_alarm_catalog
            catalog = _load_alarm_catalog()
            return {"ok": True, "names": sorted(catalog.keys())}
        except Exception as e:
            logger.error(f"get_alarm_catalog error: {e}")
            return {"ok": False, "error": str(e), "names": []}

    def get_alarm_filter(self, event_id: str) -> dict:
        """Tipos de alarme atualmente coletados para o evento (default se não definido)."""
        try:
            from core.collector import _DEFAULT_ALARM_NAMES
            config = db.get_event(event_id) or _active_event or {}
            oss = config.get("oss", {}) or {}
            names = oss.get("alarm_filter") or list(_DEFAULT_ALARM_NAMES)
            return {"ok": True, "names": names}
        except Exception as e:
            logger.error(f"get_alarm_filter error: {e}")
            return {"ok": False, "error": str(e), "names": []}

    def set_alarm_filter(self, event_id: str, names: list) -> dict:
        """Persiste alarm_filter na config do evento e recoleta na hora (se ativo)."""
        try:
            if not isinstance(names, list):
                return {"ok": False, "error": "names deve ser uma lista de nomes."}
            config = db.get_event(event_id)
            if not config:
                return {"ok": False, "error": "Evento não encontrado"}
            oss = config.get("oss", {}) or {}
            oss["alarm_filter"] = names
            config["oss"] = oss
            db.save_event(config)
            try:
                db.export_event_to_server(config)
            except Exception as ex:
                logger.warning(f"set_alarm_filter: falha ao exportar evento: {ex}")

            global _active_event
            if _active_event and _active_event.get("id") == event_id:
                _active_event.setdefault("oss", {})["alarm_filter"] = names

            # Reflete no coletor ativo (lê oss.alarm_filter a cada ciclo) e recoleta já.
            if (scheduler.is_recording and scheduler._event_config
                    and scheduler._event_config.get("id") == event_id):
                scheduler._event_config.setdefault("oss", {})["alarm_filter"] = names
                coll = scheduler._collector
                if coll is not None and isinstance(getattr(coll, "event", None), dict):
                    coll.event.setdefault("oss", {})["alarm_filter"] = names
                try:
                    scheduler.collect_alarms_now()
                except Exception as ex:
                    logger.warning(f"set_alarm_filter: falha ao recoletar alarmes: {ex}")
            return {"ok": True, "names": names}
        except Exception as e:
            logger.error(f"set_alarm_filter error: {e}")
            return {"ok": False, "error": str(e)}

    def refresh_alarms(self, event_id: str) -> dict:
        """Força uma coleta imediata de alarmes (botão de refresh do painel)."""
        try:
            if not scheduler.is_recording:
                return {"ok": False, "error": "Coleta não está ativa para este evento."}
            count = scheduler.collect_alarms_now()
            return {"ok": True, "count": count}
        except Exception as e:
            logger.error(f"refresh_alarms error: {e}")
            return {"ok": False, "error": str(e)}

    # ── Alertas ──────────────────────────────────────────────────────

    def get_alerts(self, event_id: str, timestamp: Optional[str] = None) -> list:
        try:
            return db.get_active_alerts(event_id, timestamp)
        except Exception as e:
            logger.error(f"get_alerts error: {e}")
            return []

    def get_event_timestamps(self, event_id: str) -> list:
        try:
            return db.get_event_timestamps(event_id)
        except Exception as e:
            logger.error(f"get_event_timestamps error: {e}")
            return []

    def acknowledge_alert(self, alert_id: int) -> dict:
        try:
            event_id = None
            if _active_event:
                event_id = _active_event.get("id")
            if not event_id:
                events = db.get_events(status="ACTIVE")
                if events:
                    event_id = events[0]["id"]
            if not event_id:
                return {"ok": False, "error": "Nenhum evento ativo para reconhecer o alerta"}

            db.acknowledge_alert(event_id, alert_id)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def acknowledge_all_alerts(self, event_id: str) -> dict:
        try:
            db.acknowledge_all_alerts(event_id)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_all_alerts(self, event_id: str) -> dict:
        try:
            db.delete_all_alerts(event_id)
            return {"ok": True}
        except Exception as e:
            logger.error(f"delete_all_alerts error: {e}")
            return {"ok": False, "error": str(e)}

    def download_alerts_log(self, event_id: str) -> dict:
        try:
            alerts = db.get_all_alerts(event_id)
            if not alerts:
                return {"ok": False, "error": "Nenhum alerta disponível para download neste evento."}

            import os
            from pathlib import Path
            downloads_dir = Path.home() / "Downloads"
            if os.name == 'nt':
                try:
                    import winreg
                    sub_key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                        downloads_dir = Path(winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0])
                except Exception:
                    pass

            downloads_dir.mkdir(parents=True, exist_ok=True)
            filename = f"smart_events_alerts_{event_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            file_path = downloads_dir / filename

            with open(file_path, "w", encoding="utf-8") as f:
                for a in alerts:
                    ack_status = "LIDO" if a.get("acknowledged") else "ATIVO"
                    site = a.get("site_id") or "GLOBAL"
                    cell = f"/{a.get('cell_id')}" if a.get("cell_id") else ""
                    f.write(
                        f"[{a.get('timestamp')}] [{a.get('severity')}] [{a.get('level')}] "
                        f"[{site}{cell}] {a.get('message')} (status={ack_status})\n"
                    )

            return {"ok": True, "path": str(file_path)}
        except Exception as e:
            logger.error(f"download_alerts_log error: {e}")
            return {"ok": False, "error": str(e)}

    def silence_alert(self, alert_key: str) -> dict:
        try:
            db.silence_alert(alert_key)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Logs de coleta (painel do desenvolvedor </>) ─────────────────

    @staticmethod
    def _resolve_downloads_dir():
        """Resolve a pasta Downloads do usuário (com fallback no Windows via registro)."""
        import os
        from pathlib import Path
        downloads_dir = Path.home() / "Downloads"
        if os.name == "nt":
            try:
                import winreg
                sub_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                    downloads_dir = Path(winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
            except Exception:
                pass
        downloads_dir.mkdir(parents=True, exist_ok=True)
        return downloads_dir

    def get_collection_logs(self, limit: int = 800) -> dict:
        """Retorna os logs de coleta capturados em memória (collector, scheduler, renovação)."""
        try:
            return {"ok": True, "logs": log_buffer.get_records(limit)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def clear_collection_logs(self) -> dict:
        """Limpa o buffer de logs de coleta."""
        try:
            log_buffer.clear()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def download_collection_logs(self) -> dict:
        """Salva os logs de coleta atuais em um arquivo .log na pasta Downloads."""
        try:
            records = log_buffer.get_records(0)  # tudo
            if not records:
                return {"ok": False, "error": "Nenhum log de coleta disponível."}

            downloads_dir = self._resolve_downloads_dir()
            filename = f"smart_events_coleta_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            file_path = downloads_dir / filename
            with open(file_path, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(f"[{r['ts']}] [{r['level']}] [{r['logger']}] {r['msg']}\n")
            return {"ok": True, "path": str(file_path)}
        except Exception as e:
            logger.error(f"download_collection_logs error: {e}")
            return {"ok": False, "error": str(e)}

    def capture_diagnostics(self) -> dict:
        """Captura a próxima resposta de Monitoring e devolve o arquivo gerado.

        A coleta é executada pelo scheduler para manter a mesma persistência, status e
        regras de cursor de um ciclo normal. A opção é consumida nesta única resposta.
        """
        collector = None
        try:
            from core.collector import HttpCollector

            collector = scheduler._collector
            if not scheduler.is_recording or not isinstance(collector, HttpCollector):
                return {"ok": False, "error": "Ative um evento conectado ao OSS para capturar o diagnóstico."}
            collector.arm_raw_capture("monitoring")
            scheduler._collect_kpis()
            path = collector._last_raw_dumps.get("monitoring")
            if not path:
                return {"ok": False, "error": "O OSS não devolveu uma resposta de Monitoring para capturar."}
            return {"ok": True, "path": str(path)}
        except Exception as e:
            logger.error(f"capture_diagnostics error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            # Se o OSS falhar antes de responder, não deixe a solicitação escapar
            # para um ciclo futuro e inesperado.
            if hasattr(collector, "disarm_raw_capture"):
                collector.disarm_raw_capture("monitoring")

    # ── Estado da aplicação ──────────────────────────────────────────

    def get_app_status(self) -> dict:
        return {
            "recording":  scheduler.is_recording,
            "db_size_mb": db.get_db_size_mb(),
            "now":        datetime.utcnow().isoformat(),
        }

    def get_collection_status(self) -> dict:
        """Contrato operacional da coleta; ``ok`` só descreve esta chamada local."""
        try:
            status = scheduler.get_status()
            coll = scheduler._collector
            active_oss = (_active_event or {}).get("oss", {}) if _active_event else {}
            region = getattr(coll, "_region", "") or active_oss.get("region", "") or ""
            host = getattr(coll, "_session_host", "") or ""
            fars_contract = None
            needs_interactive = False
            interactive_modules = set()
            try:
                from core.collector import HttpCollector
                if isinstance(coll, HttpCollector):
                    interactive_modules = HttpCollector.interactive_modules_for(coll.base_url)
                    needs_interactive = bool(interactive_modules)
                    fars_contract = coll._trace_contract()
            except Exception:
                pass
            kpi = status.get("kpi", {"state": "idle"})
            vip = dict(status.get("vip", {"state": "idle"}))
            vip["task_causes"] = [
                {
                    "task_id": item.get("details", {}).get("task_id"),
                    "vip": item.get("details", {}).get("vip"),
                    "cause": item.get("message") or vip.get("cause"),
                    "code": item.get("code"),
                }
                for item in vip.get("diagnostics", [])
                if item.get("details", {}).get("task_id") is not None
            ]
            alarms = status.get("alarms", {"state": "idle"})

            def module_state(names, fallback):
                if any(name in interactive_modules for name in names):
                    return "auth_required"
                states = [item.get("state", "idle") for item in fallback]
                for state in ("auth_required", "error", "stale", "partial", "running", "data", "empty"):
                    if state in states:
                        return state
                return "idle"

            monitoring_state = module_state(("monitoring",), (kpi, alarms))
            trace_state = module_state(("trace",), (vip,))
            states = [kpi.get("state"), vip.get("state"), alarms.get("state"), monitoring_state, trace_state]
            overall_state = next((candidate for candidate in
                                  ("auth_required", "error", "stale", "partial", "running", "data", "empty")
                                  if candidate in states), "idle")
            return {
                "ok":         True,
                "recording":  scheduler.is_recording,
                "overall_state": overall_state,
                "kpi":        kpi,
                "vip":        vip,
                "alarms":     alarms,
                "session":    {
                    "needs_interactive": needs_interactive,
                    "region": region,
                    "host": host,
                    "fars_contract": fars_contract,
                    "monitoring": {"state": monitoring_state},
                    "trace": {"state": trace_state},
                },
                "now":        datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"get_collection_status error: {e}")
            return {"ok": False, "error": str(e)}

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
        """Remove sites e vips do payload (inclui task_ids de integração) antes de enviar ao frontend."""
        if isinstance(event, str):
            try:
                event = json.loads(event)
            except Exception:
                return {}

        out = dict(event)

        # Garante que polygon seja um objeto Python (list) caso venha do banco como string
        if isinstance(out.get("polygon"), str):
            try:
                out["polygon"] = json.loads(out["polygon"])
            except Exception:
                pass

        # Remove config_json se vier do banco
        out.pop("config_json", None)
        
        # Remove listagens massivas de sites e vips do payload de metadados
        out.pop("sites", None)
        out.pop("vips", None)
        
        return out

    def _aggregate_utilization(self, kpi_rows: list) -> dict:
        """Calcula utilização máxima por site a partir das células (incluindo DL/UL)."""
        agg = {}
        for r in kpi_rows:
            if r["metric"] not in ("utilization", "utilization_dl", "utilization_ul"):
                continue
            site = r["site_id"]
            if site not in agg or r["value"] > agg[site]:
                agg[site] = r["value"]
        return agg

    def _aggregate_metric_for_list(self, kpi_rows: list, metric: str) -> dict:
        """
        Agrega valores de células para exibição na coluna da lista de sites.
        Retorna dict: { site_id: float }
        
        Regras:
        - user_count / traffic_volume_*: retorna soma das células (share calculado depois)
        - accessibility: mínimo das células (pior caso)
        - rsrp / rsrq: média das células
        - outros (utilização, throughput): máximo das células (pior caso)
        """
        VOLUME_METRICS = {"user_count", "traffic_volume_dl", "traffic_volume_ul"}
        MEAN_METRICS   = {"rsrp", "rsrq", "throughput_dl", "throughput_ul"}
        MIN_METRICS    = {"accessibility"}

        # Acumula valores por site
        site_vals = {}
        for r in kpi_rows:
            if r.get("metric") != metric:
                continue
            site = r["site_id"]
            val = r.get("value")
            if val is None:
                continue
            if site not in site_vals:
                site_vals[site] = []
            site_vals[site].append(val)

        result = {}
        total = 0.0

        for site, vals in site_vals.items():
            if not vals:
                continue
            if metric in VOLUME_METRICS:
                agg = sum(vals)
                total += agg
            elif metric in MIN_METRICS:
                agg = min(vals)
            elif metric in MEAN_METRICS:
                agg = sum(vals) / len(vals)
            else:
                agg = max(vals)  # utilization_dl, utilization_ul
            result[site] = agg

        # Para volume: converte soma em share percentual
        if metric in VOLUME_METRICS and total > 0:
            return {site: round((val / total) * 100, 1)
                    for site, val in result.items()}
        
        return {site: round(val, 1) for site, val in result.items()}

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

    def clear_event_history(self, event_id: str) -> dict:
        """Limpa o histórico de medições e alertas do evento atual."""
        try:
            db.clear_event_history(event_id)
            # Se o scheduler estiver ativo e coletando o evento atual, limpa a memória do coletor de CSV
            if scheduler.is_recording and scheduler._event_config and scheduler._event_config.get("id") == event_id:
                if hasattr(scheduler._collector, "_processed"):
                    scheduler._collector._processed.clear()
            return {"ok": True}
        except Exception as e:
            logger.error(f"clear_event_history error: {e}")
            return {"ok": False, "error": str(e)}

