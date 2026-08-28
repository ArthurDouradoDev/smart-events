"""
Api: métodos Python expostos ao JavaScript via window.pywebview.api.
Todos os métodos retornam dicts/lists serializáveis para JSON.
"""

import json
import logging
import math
import os
import re
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
from core.kpi_formulas import catalog_for_api, threshold_value, threshold_object

logger = logging.getLogger(__name__)

_active_event: Optional[dict] = None
_update_callback = None  # função JS chamada quando novos dados chegam
_activation_lock = threading.RLock()


# A composicao da visao geral e deliberadamente explicita. Ela representa os
# nove KPIs do bloco Monitoring 4G e os treze KPIs 5G distribuidos em nove
# paineis no frontend (quatro deles sao pares DL/UL).
KPI_OVERVIEW_METRICS = {
    "4G": (
        "accessibility", "availability", "drop_rate", "utilization_dl",
        "utilization_ul", "interference_ul", "throughput_dl",
        "throughput_ul", "user_count",
    ),
    "5G": (
        "accessibility", "drop_rate", "user_count", "availability",
        "interference_ul", "utilization_dl", "utilization_ul",
        "throughput_dl", "throughput_ul", "traffic_volume_dl_sa",
        "traffic_volume_ul_sa", "traffic_volume_dl_nsa",
        "traffic_volume_ul_nsa",
    ),
}

# Teto de escopos comparados de uma vez na visao geral. Cada escopo custa uma
# varredura completa das metricas do painel, e a paleta categorica do app tem
# oito cores estaveis — acima disso as linhas deixam de ser distinguiveis.
KPI_OVERVIEW_MAX_SCOPES = 8

# Paleta dos clusters automáticos por portadora, defasada da do cadastro
# (que começa em vermelho/azul) para não colidir quando os dois tipos
# aparecem juntos na visão geral.
_EARFCN_CLUSTER_COLORS = (
    "#3FB950", "#D29922", "#ab7df6", "#f692cc",
    "#00d2ff", "#FF7B00", "#F85149", "#388BFD",
)


# Consolidacao entre escopos: o melhor motivo vence (ver get_kpi_overview_multi).
_REASON_RANK = {"ok": 0, "no_traffic": 1, "no_data": 2}


def _overview_reasons(labels: list, metrics: dict) -> dict:
    """B6 — por que um painel esta vazio: falta de coleta ou falta de trafego.

    Sao coisas diferentes e a tela precisa distinguir. A janela so tem
    ``labels`` quando alguma metrica da familia produziu valor ali, entao
    ``labels`` vazio significa que nao houve coleta ("no_data"). Com coleta na
    janela, uma metrica sem nenhum ponto e uma metrica que ficou **indefinida**
    — denominador zero, isto e, nenhuma tentativa no minuto ("no_traffic"), que
    e o caso normal do SA no 5G.

    A leitura nao tem acesso ao ``not_applicable`` do ciclo de coleta: ele e
    contado em memoria pelo collector e nunca foi persistido por metrica. A
    ausencia de ponto na grade e o mesmo fato visto do banco.
    """
    collected = bool(labels)
    return {
        metric: (
            "ok" if any(value is not None for value in values)
            else "no_traffic" if collected
            else "no_data"
        )
        for metric, values in metrics.items()
    }


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

    @staticmethod
    def _technology_family(technology: str | None) -> str | None:
        if technology == "4G":
            return "4G"
        if technology in {"5G_NRCELL", "5G_NRDUCELL"}:
            return "5G"
        return None

    @staticmethod
    def _cell_technology_family(cell) -> str | None:
        cell_id = cell if isinstance(cell, str) else cell.get("id", "")
        declared = "" if isinstance(cell, str) else cell.get("tech", "")
        text = f"{declared or ''} {cell_id or ''}".upper()
        has_4g = bool(re.search(r"(^|[^A-Z0-9])(?:4G|LTE)([^A-Z0-9]|$)", text))
        has_5g = bool(re.search(r"(^|[^A-Z0-9])(?:5G|NR|NCI)([^A-Z0-9]|$)", text))
        if has_4g == has_5g:
            return None
        return "4G" if has_4g else "5G"

    @classmethod
    def _filter_cells_for_family(cls, cells: list, family: str | None) -> list:
        if not family:
            return list(cells or [])
        return [cell for cell in (cells or [])
                if cls._cell_technology_family(cell) in (family, None)]

    @classmethod
    def _single_configured_family(cls, config: dict) -> str | None:
        families = {
            family for technology in cls._configured_kpi_technologies(config)
            if (family := cls._technology_family(technology))
        }
        return next(iter(families)) if len(families) == 1 else None

    _MERGE_DISTANCE_M = 50.0
    # Prefixo de tecnologia usado pela EP no nome do site (Salvador: SR-/SD- no 4G,
    # 5G-/5D- no 5G). Sites gêmeos como "SR-SACAL5" e "5G-SACAL5" só têm esse token
    # como diferença — sem removê-lo a fusão nunca via mesmo nome e o par ficava
    # duplicado no mapa/KPIs. Eventos com nome já igual entre 4G/5G (ex. "SPSMG7")
    # não têm esse prefixo e continuam batendo como antes.
    _SITE_NAME_PREFIX_RE = re.compile(r"^(?:4G|5G|5D|SD|SR)-")

    @classmethod
    def _normalize_site_name(cls, name) -> str:
        normalized = str(name or "").strip().upper()
        return cls._SITE_NAME_PREFIX_RE.sub("", normalized, count=1)

    @staticmethod
    def _display_site_name(name) -> str:
        """Nome de exibição a partir do ``nename`` da EP (convenção TIM).

        A rede usa duas formas de nomear site: sem hífen, o nome já é o do site;
        com hífen, o que vem antes é o indicador de tecnologia e o que vem depois
        é o nome (``5D-SACEO1`` → ``SACEO1``). O nome bruto não se perde — segue
        em ``original_name`` no site fundido e intacto no banco.
        """
        text = str(name or "").strip()
        if "-" not in text:
            return text
        return text.rsplit("-", 1)[-1].strip() or text

    @staticmethod
    def _distance_m(a: dict, b: dict) -> float | None:
        try:
            lat1, lng1 = float(a["lat"]), float(a["lng"])
            lat2, lng2 = float(b["lat"]), float(b["lng"])
        except (TypeError, ValueError, KeyError):
            return None
        radius = 6_371_000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlmb = math.radians(lng2 - lng1)
        h = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2)
        return 2 * radius * math.asin(min(1.0, math.sqrt(h)))

    @classmethod
    def _annotate_cell(cls, cell) -> dict:
        if isinstance(cell, str):
            return {"id": cell, "label": cell, "family": cls._cell_technology_family(cell)}
        out = dict(cell)
        out["family"] = cls._cell_technology_family(cell)
        if "label" not in out:
            out["label"] = out.get("id", "")
        return out

    @classmethod
    def _member_family(cls, site: dict) -> str | None:
        families = {
            cls._cell_technology_family(cell)
            for cell in (site.get("cells") or [])
        }
        families.discard(None)
        return next(iter(families)) if len(families) == 1 else None

    @classmethod
    def _as_merged_site(cls, cluster: list[dict], site_id: str) -> dict:
        first = cluster[0]
        raw_name = first.get("name") or site_id
        name = cls._display_site_name(raw_name) or raw_name
        cells = []
        members = []
        lats: list[float] = []
        lngs: list[float] = []
        is_event_site = False
        for site in cluster:
            members.append({"site_id": site["id"], "family": cls._member_family(site)})
            for cell in site.get("cells") or []:
                cells.append(cls._annotate_cell(cell))
            if site.get("is_event_site", True):
                is_event_site = True
            try:
                lats.append(float(site["lat"]))
                lngs.append(float(site["lng"]))
            except (TypeError, ValueError, KeyError):
                pass
        families = []
        for family in ("4G", "5G"):
            if (any(member.get("family") == family for member in members)
                    or any(cell.get("family") == family for cell in cells)):
                families.append(family)
        return {
            "id": site_id,
            "name": name,
            "original_name": raw_name,
            "lat": (sum(lats) / len(lats)) if lats else first.get("lat"),
            "lng": (sum(lngs) / len(lngs)) if lngs else first.get("lng"),
            "members": members,
            "tech_families": families,
            "cells": cells,
            "is_event_site": is_event_site,
        }

    @classmethod
    def _merged_sites(cls, config: dict) -> list[dict]:
        """Única fonte de fusão 4G/5G: nome normalizado + coordenada ≤ 50 m."""
        raw_sites = list((config or {}).get("sites") or [])
        by_name: dict[str, list[dict]] = {}
        unnamed: list[dict] = []
        for site in raw_sites:
            key = cls._normalize_site_name(site.get("name") or "")
            if not key:
                unnamed.append(site)
                continue
            by_name.setdefault(key, []).append(site)

        result: list[dict] = []
        used_ids: set[str] = set()

        def _unique_id(desired: str, fallback: str) -> str:
            candidate = desired or fallback
            if candidate not in used_ids:
                used_ids.add(candidate)
                return candidate
            suffix = 2
            while f"{candidate}#{suffix}" in used_ids:
                suffix += 1
            unique = f"{candidate}#{suffix}"
            used_ids.add(unique)
            return unique

        for name, group in by_name.items():
            clusters: list[list[dict]] = []
            for site in group:
                placed = False
                for cluster in clusters:
                    if any(
                        (dist := cls._distance_m(site, member)) is not None
                        and dist <= cls._MERGE_DISTANCE_M
                        for member in cluster
                    ):
                        cluster.append(site)
                        placed = True
                        break
                if not placed:
                    clusters.append([site])

            if len(clusters) > 1:
                for i, left in enumerate(clusters):
                    for right in clusters[i + 1:]:
                        a, b = left[0], right[0]
                        logger.warning(
                            "Sites homônimos não fundidos (coordenada > %.0fm): "
                            "%s %s (%s,%s) e %s %s (%s,%s)",
                            cls._MERGE_DISTANCE_M,
                            a.get("id"), a.get("name"), a.get("lat"), a.get("lng"),
                            b.get("id"), b.get("name"), b.get("lat"), b.get("lng"),
                        )

            for cluster in clusters:
                if len(cluster) > 1:
                    site_id = _unique_id(name, cluster[0]["id"])
                else:
                    site_id = _unique_id(str(cluster[0]["id"]), name)
                result.append(cls._as_merged_site(cluster, site_id))

        for site in unnamed:
            result.append(cls._as_merged_site([site], _unique_id(str(site["id"]), "site")))

        order = {site["id"]: i for i, site in enumerate(raw_sites)}
        result.sort(key=lambda site: min(
            (order.get(member["site_id"], 10**9) for member in site["members"]),
            default=10**9,
        ))
        return result

    @classmethod
    def _find_merged_site(cls, merged: list[dict], site_id: str) -> dict | None:
        if not site_id:
            return None
        for site in merged:
            if site["id"] == site_id:
                return site
            if any(member.get("site_id") == site_id for member in site.get("members") or []):
                return site
        return None

    @classmethod
    def _cluster_merged_site_ids(cls, cluster: dict, merged: list[dict]) -> list[str]:
        """Resolve membros antigos e granulares para ids físicos fundidos únicos."""
        seen: list[str] = []
        stored_ids = list(cluster.get("site_ids") or [])
        stored_ids.extend(
            member.get("site_id")
            for member in (cluster.get("members") or [])
            if member.get("site_id")
        )
        for raw_id in stored_ids:
            site = cls._find_merged_site(merged, raw_id)
            merged_id = site["id"] if site else raw_id
            if merged_id not in seen:
                seen.append(merged_id)
        return seen

    @classmethod
    def _cluster_raw_selections(
            cls, cluster: dict, merged: list[dict], config: dict) -> list[dict]:
        """Expande um cluster para sites crus e células selecionadas.

        ``cell_ids=None`` significa site inteiro. Clusters legados, que possuem
        somente ``site_ids``, continuam selecionando o site inteiro. O campo novo
        ``members`` é autoritativo quando presente e permite seleção parcial.
        """
        raw_sites = {
            str(site.get("id")): site
            for site in (config or {}).get("sites") or []
            if site.get("id") is not None
        }
        selections: dict[str, set[str] | None] = {}

        def _add(raw_id, cell_ids=None, all_cells=False):
            raw_id = str(raw_id)
            if raw_id in selections and selections[raw_id] is None:
                return
            raw_site = raw_sites.get(raw_id)
            known_cells = {
                str(cell if isinstance(cell, str) else cell.get("id"))
                for cell in (raw_site or {}).get("cells") or []
                if (cell if isinstance(cell, str) else cell.get("id")) is not None
            }
            requested = {str(cell_id) for cell_id in (cell_ids or []) if cell_id is not None}
            if all_cells or cell_ids is None or (known_cells and known_cells <= requested):
                selections[raw_id] = None
                return
            if not requested:
                return
            current = selections.setdefault(raw_id, set())
            if current is not None:
                current.update(requested)

        def _expand(stored_id, cell_ids=None, all_cells=False):
            stored_id = str(stored_id)
            if stored_id in raw_sites:
                _add(stored_id, cell_ids, all_cells)
                return
            merged_site = cls._find_merged_site(merged, stored_id)
            targets = list((merged_site or {}).get("members") or [])
            if not targets:
                _add(stored_id, cell_ids, all_cells)
                return
            if all_cells or cell_ids is None:
                for target in targets:
                    _add(target["site_id"], None, True)
                return
            remaining = {str(cell_id) for cell_id in cell_ids or []}
            for target in targets:
                raw_site = raw_sites.get(str(target["site_id"])) or {}
                owned = {
                    str(cell if isinstance(cell, str) else cell.get("id"))
                    for cell in raw_site.get("cells") or []
                    if (cell if isinstance(cell, str) else cell.get("id")) is not None
                }
                matched = remaining & owned
                if matched:
                    _add(target["site_id"], matched)
                    remaining -= matched
            if remaining and len(targets) == 1:
                _add(targets[0]["site_id"], remaining)

        if "members" in cluster:
            for member in cluster.get("members") or []:
                if member.get("site_id") is None:
                    continue
                all_cells = bool(member.get("all_cells"))
                cell_ids = None if all_cells else member.get("cell_ids", [])
                _expand(member["site_id"], cell_ids, all_cells)
        else:
            for site_id in cluster.get("site_ids") or []:
                # Contrato legado da Fase 1: um id cru de qualquer gêmeo 4G/5G
                # representava o site físico fundido inteiro.
                merged_site = cls._find_merged_site(merged, str(site_id))
                targets = list((merged_site or {}).get("members") or [])
                if targets:
                    for target in targets:
                        _add(target["site_id"], None, True)
                else:
                    _expand(site_id, None, True)

        return [
            {"site_id": site_id,
             "cell_ids": None if cell_ids is None else sorted(cell_ids)}
            for site_id, cell_ids in selections.items()
        ]

    def _site_carrier_selections(
            self, config: dict, merged: list[dict], site_id: str, earfcn: str,
            family: str | None) -> list[dict]:
        """Recorta um site fundido pelas células de uma portadora (EARFCN).

        Devolve seleções com ids de site **brutos** (membros), como
        :meth:`_cluster_series_by_family` espera — nunca o id fundido.
        """
        site = self._find_merged_site(merged, site_id)
        if not site:
            return []
        by_owner: dict[str, list[str]] = {}
        for cell in site.get("cells") or []:
            if self._cell_earfcn(cell) != earfcn:
                continue
            cell_family = cell.get("family") or self._single_configured_family(config)
            if family and cell_family and cell_family != family:
                continue
            cell_id = cell.get("id")
            if not cell_id:
                continue
            owner = self._owner_site_id_for_cell(site, cell_id, site_id)
            by_owner.setdefault(owner, []).append(str(cell_id))
        return [
            {"site_id": owner, "cell_ids": cell_ids}
            for owner, cell_ids in by_owner.items()
        ]

    @classmethod
    def _cell_earfcn(cls, cell) -> str | None:
        if not isinstance(cell, dict):
            return None
        value = cell.get("earfcn")
        if value is None or value == "":
            return None
        try:
            numeric = float(value)
            if not math.isfinite(numeric) or not numeric.is_integer() or numeric < 0:
                return None
            return str(int(numeric))
        except (TypeError, ValueError):
            text = str(value).strip()
            return text if text.isdigit() else None

    @classmethod
    def _earfcn_clusters(cls, config: dict) -> list[dict]:
        """Clusters sintéticos: uma portadora (DLEARFCN/NR-ARFCN) = um recorte de células.

        4G e 5G entram — o NR-ARFCN chega na mesma coluna ``DLEARFCN`` da EP.
        O id é estável (``earfcn-<valor>``) para o dashboard e a visão geral
        reencontrarem o mesmo escopo entre ciclos; o pressuposto é que os
        espaços numéricos de LTE EARFCN e NR-ARFCN não colidem.
        """
        grouped: dict[str, dict[str, list[str]]] = {}
        families: dict[str, set[str]] = {}
        for site in (config or {}).get("sites") or []:
            site_id = site.get("id")
            if site_id is None:
                continue
            site_id = str(site_id)
            for cell in site.get("cells") or []:
                earfcn = cls._cell_earfcn(cell)
                if not earfcn:
                    continue
                cell_id = cell if isinstance(cell, str) else cell.get("id")
                if not cell_id:
                    continue
                grouped.setdefault(earfcn, {}).setdefault(site_id, []).append(str(cell_id))
                family = cls._cell_technology_family(cell) or cls._single_configured_family(config)
                if family:
                    families.setdefault(earfcn, set()).add(family)

        clusters = []
        ordered = sorted(grouped, key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
        color_index: dict[str | None, int] = {}
        for earfcn in ordered:
            members = [
                {"site_id": site_id, "cell_ids": cell_ids}
                for site_id, cell_ids in grouped[earfcn].items()
            ]
            earfcn_families = families.get(earfcn) or set()
            family = next(iter(earfcn_families)) if len(earfcn_families) == 1 else None
            index = color_index.get(family, 0)
            color_index[family] = index + 1
            clusters.append({
                "id": f"earfcn-{earfcn}",
                "name": f"Portadora {earfcn}",
                "color": _EARFCN_CLUSTER_COLORS[index % len(_EARFCN_CLUSTER_COLORS)],
                "source": "earfcn",
                "family": family,
                "members": members,
                "polygon": [],
            })
        return clusters

    @classmethod
    def _site_carriers(cls, cells: list, config: dict) -> list[dict]:
        """Portadoras (EARFCN) presentes num conjunto de células, para `get_sites`."""
        grouped: dict[str, list] = {}
        for cell in cells or []:
            earfcn = cls._cell_earfcn(cell)
            if not earfcn:
                continue
            grouped.setdefault(earfcn, []).append(cell)
        ordered = sorted(grouped, key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
        carriers = []
        for earfcn in ordered:
            group_cells = grouped[earfcn]
            families = {
                cell.get("family") or cls._single_configured_family(config)
                for cell in group_cells
            }
            families.discard(None)
            family = next(iter(families)) if len(families) == 1 else None
            carriers.append({
                "earfcn": earfcn,
                "family": family,
                "cell_count": len(group_cells),
            })
        return carriers

    @classmethod
    def _clusters_of(cls, config: dict) -> list[dict]:
        """Clusters cadastrados + portadoras geradas a partir do DLEARFCN.

        Um cluster salvo com o mesmo id (``earfcn-1276``) ganha da geração
        automática — o operador pode ter editado nome/cor.
        """
        configured = [c for c in (config or {}).get("clusters") or [] if c.get("id")]
        seen = {c["id"] for c in configured}
        extra = [c for c in cls._earfcn_clusters(config) if c["id"] not in seen]
        return configured + extra

    @classmethod
    def _find_cluster(cls, config: dict, cluster_id: str) -> dict | None:
        if not cluster_id:
            return None
        for cluster in cls._clusters_of(config):
            if cluster.get("id") == cluster_id:
                return cluster
        return None

    @staticmethod
    def _combine_family_values(metric: str, values: list) -> float | None:
        vals = [value for value in values if value is not None]
        if not vals:
            return None
        if any(token in metric for token in ("availability", "accessibility", "rsrp", "rsrq")):
            return sum(vals) / len(vals)
        if "throughput" in metric or "traffic_volume" in metric:
            return sum(vals)
        return max(vals)

    @classmethod
    def _align_kpi_series(cls, series: list[dict]) -> tuple[list[dict], list]:
        common = sorted({ts for item in series for ts in (item.get("labels") or [])})
        aligned = []
        for item in series:
            lookup = dict(zip(item.get("labels") or [], item.get("values") or []))
            aligned.append({
                "technology": item.get("technology"),
                "labels": common,
                "values": [lookup.get(ts) for ts in common],
            })
        return aligned, common

    def _index_original_to_merged(self, merged: list[dict]) -> dict:
        index = {}
        for site in merged:
            for member in site.get("members") or []:
                index[member["site_id"]] = (site["id"], member.get("family"))
        return index

    def _remap_metric_by_family(self, values_by_original: dict, index: dict) -> dict:
        remapped = {}
        for original_id, value in values_by_original.items():
            mapped = index.get(original_id)
            if mapped:
                remapped[mapped] = value
            else:
                remapped[(original_id, None)] = value
        return remapped

    def _metric_value_for_merged(
            self, metric_by_key: dict, merged_id: str, families: list,
            display_family: str | None, metric: str):
        if display_family:
            return metric_by_key.get((merged_id, display_family))
        values = [
            metric_by_key[(merged_id, family)]
            for family in (families or [None])
            if (merged_id, family) in metric_by_key
        ]
        if not values:
            return metric_by_key.get((merged_id, None))
        if len(values) == 1:
            return values[0]
        return self._combine_family_values(metric, values)

    def get_sites(self, event_id: str, timestamp: Optional[str] = None,
                  metric: str = "utilization_dl",
                  technology_family: Optional[str] = None) -> list:
        """Retorna sites com status atual para renderização no mapa."""
        config = (_active_event if _active_event and _active_event.get("id") == event_id else None)
        try:
            config = db.get_event(event_id) or config
            if not config:
                return []

            merged = self._merged_sites(config)
            original_index = self._index_original_to_merged(merged)

            latest = db.get_latest_kpi(event_id, timestamp)
            util_by_original = self._aggregate_utilization(latest)

            if metric != "utilization_dl":
                latest_metric = db.get_latest_kpi_by_metric(event_id, metric, timestamp)
            else:
                latest_metric = latest

            metric_by_original = self._aggregate_metric_for_list(latest_metric, metric)
            metric_by_key = self._remap_metric_by_family(metric_by_original, original_index)
            util_by_key = self._remap_metric_by_family(util_by_original, original_index)

            # Dados novos possuem uma linha SITE por (site_id, technology); a
            # chave (merged_id, family) impede que 4G e 5G se sobrescrevam.
            persisted_metric = db.get_latest_site_kpi_by_metric(event_id, metric, timestamp)
            for row in persisted_metric:
                mapped = original_index.get(row["site_id"])
                if not mapped:
                    continue
                merged_id, member_family = mapped
                family = self._technology_family(row.get("technology")) or member_family
                metric_by_key[(merged_id, family)] = row["value"]

            persisted_util = db.get_latest_site_kpi_by_metric(
                event_id, "utilization_dl", timestamp)
            for row in persisted_util:
                mapped = original_index.get(row["site_id"])
                if not mapped:
                    continue
                merged_id, member_family = mapped
                family = self._technology_family(row.get("technology")) or member_family
                util_by_key[(merged_id, family)] = row["value"]

            sites_out = []
            configured_family = self._single_configured_family(config)
            user_family = technology_family if technology_family in ("4G", "5G") else None
            cell_family = user_family or configured_family
            thresholds = config.get("thresholds", {})
            warn = threshold_value(thresholds.get("utilization_warning"), 80)
            crit = threshold_value(thresholds.get("utilization_critical"), 95)

            cluster_membership: dict[str, list[str]] = {}
            for cluster in self._clusters_of(config):
                cluster_id = cluster.get("id")
                if not cluster_id:
                    continue
                for merged_id in self._cluster_merged_site_ids(cluster, merged):
                    cluster_membership.setdefault(merged_id, []).append(cluster_id)

            for site in merged:
                visible_cells = self._filter_cells_for_family(
                    site.get("cells", []), cell_family)
                if cell_family and not visible_cells:
                    continue
                util = self._metric_value_for_merged(
                    util_by_key, site["id"], site.get("tech_families") or [],
                    user_family, "utilization_dl")
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
                    "original_name":   site.get("original_name") or site["name"],
                    "lat":             site["lat"],
                    "lng":             site["lng"],
                    "cells":           visible_cells,
                    "members":         site.get("members") or [],
                    "tech_families":   site.get("tech_families") or [],
                    "status":          status,
                    "utilization":     round(util, 1) if util is not None else None,
                    "metric_value":    self._metric_value_for_merged(
                        metric_by_key, site["id"], site.get("tech_families") or [],
                        user_family, metric or "utilization_dl"),
                    "metric_is_share": False,
                    "is_event_site":   site.get("is_event_site", True),
                    "cluster_ids":     cluster_membership.get(site["id"], []),
                    "carriers":        self._site_carriers(visible_cells, config),
                })

            self._sites_cache[event_id] = sites_out
            return sites_out
        except Exception as e:
            logger.error(f"get_sites error: {e}")
            cached = self._sites_cache.get(event_id)
            if cached is not None:
                return cached
            if config:
                fallback = []
                for site in self._merged_sites(config):
                    fallback.append({
                        "id": site["id"], "name": site["name"],
                        "original_name": site.get("original_name") or site["name"],
                        "lat": site["lat"], "lng": site["lng"],
                        "cells": site.get("cells", []), "status": "unknown",
                        "members": site.get("members") or [],
                        "tech_families": site.get("tech_families") or [],
                        "utilization": None, "metric_value": None,
                        "metric_is_share": False,
                        "is_event_site": site.get("is_event_site", True),
                        "cluster_ids": [],
                        "carriers": self._site_carriers(site.get("cells", []), config),
                    })
                self._sites_cache[event_id] = fallback
                return fallback
            return []

    def get_site_cells(self, event_id: str, site_id: str,
                       technology_family: Optional[str] = None) -> list:
        """
        Retorna a lista de células de um site específico.
        Usado para popular o seletor de célula no gráfico.
        Aceita o id fundido e recorta pela família quando pedida.
        """
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return []
            merged = self._merged_sites(config)
            site = self._find_merged_site(merged, site_id)
            if not site:
                return []
            configured_family = self._single_configured_family(config)
            user_family = technology_family if technology_family in ("4G", "5G") else None
            cell_family = user_family or configured_family
            cells = self._filter_cells_for_family(site.get("cells") or [], cell_family)
            out = []
            for c in cells:
                if isinstance(c, str):
                    out.append({
                        "id": c, "label": c,
                        "family": self._cell_technology_family(c),
                    })
                else:
                    out.append({
                        "id":     c.get("id", ""),
                        "label":  c.get("label") or c.get("id", ""),
                        "tech":   c.get("tech"),
                        "freq":   c.get("frequency") or c.get("freq"),
                        "family": c.get("family") or self._cell_technology_family(c),
                    })
            return out
        except Exception as e:
            logger.error(f"get_site_cells error: {e}")
            return []

    def get_event_cells(self, event_id: str, technology_family: Optional[str] = None) -> list:
        """Lista todas as células do evento (todos os sites), cada uma com o
        site dono. Alimenta o filtro por células da Visão Geral, do mesmo
        jeito que ``get_sites``/``get_clusters`` alimentam os outros dois.
        """
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return []
            merged = self._merged_sites(config)
            configured_family = self._single_configured_family(config)
            user_family = technology_family if technology_family in ("4G", "5G") else None
            cell_family = user_family or configured_family
            out = []
            for site in merged:
                cells = self._filter_cells_for_family(site.get("cells") or [], cell_family)
                for c in cells:
                    annotated = self._annotate_cell(c)
                    out.append({
                        "id":        annotated.get("id", ""),
                        "name":      annotated.get("label") or annotated.get("id", ""),
                        "family":    annotated.get("family"),
                        "site_id":   site.get("id"),
                        "site_name": site.get("name") or site.get("id"),
                    })
            return out
        except Exception as e:
            logger.error(f"get_event_cells error: {e}")
            return []

    def get_clusters(self, event_id: str) -> list:
        """Lista os clusters do evento (cadastrados no servidor central) para o
        dropdown do app, incluindo o recorte granular de células."""
        try:
            config = db.get_event(event_id) or _active_event
            if not config:
                return []
            merged = self._merged_sites(config)
            out = []
            for cluster in self._clusters_of(config):
                if not cluster.get("id"):
                    continue
                selections = self._cluster_raw_selections(cluster, merged, config)
                raw_sites = {
                    str(site.get("id")): site for site in config.get("sites") or []
                }
                cell_count = 0
                has_partial = False
                for selection in selections:
                    selected_cells = selection.get("cell_ids")
                    available = (raw_sites.get(str(selection["site_id"])) or {}).get("cells") or []
                    if selected_cells is None:
                        cell_count += len(available)
                    else:
                        cell_count += len(selected_cells)
                        has_partial = has_partial or len(selected_cells) < len(available)
                entry = {
                    "id":         cluster["id"],
                    "name":       cluster.get("name") or cluster["id"],
                    "color":      cluster.get("color"),
                    "site_count": len(self._cluster_merged_site_ids(cluster, merged)),
                    "cell_count": cell_count,
                    "has_partial_selection": has_partial,
                    "polygon":    cluster.get("polygon") or [],
                }
                if cluster.get("source"):
                    entry["source"] = cluster["source"]
                if cluster.get("family"):
                    entry["family"] = cluster["family"]
                out.append(entry)
            return out
        except Exception as e:
            logger.error(f"get_clusters error: {e}")
            return []

    # ── KPI / gráfico ────────────────────────────────────────────────

    @staticmethod
    def _configured_kpi_technologies(config: dict) -> list[str]:
        """Tipos de objeto realmente consultados pelas tasks PM do evento.

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
            technology = "4G" if tech in {"4G", "LTE"} else (
                "5G_NRCELL" if tech in {"NRCELL", "NR_CELL", "5G_NRCELL", "5G_NR_CELL"} else (
                    "5G_NRDUCELL" if tech in {"NRDUCELL", "NR_DU_CELL", "DUCELL", "DU_CELL",
                                                 "5G_NRDUCELL", "5G_NR_DU_CELL"} else None))
            if technology and technology not in technologies:
                technologies.append(technology)
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

    def _member_ids_for_family(self, members: list, family: str | None) -> list[str]:
        if not family:
            return [member["site_id"] for member in members]
        return [
            member["site_id"] for member in members
            if member.get("family") in (family, None)
        ]

    @staticmethod
    def _find_site_for_cell(merged: list[dict], cell_id: str) -> dict | None:
        """Acha o site fundido dono de uma célula, varrendo o evento inteiro.

        Usado pelo escopo ``scope="cell"`` da Visão Geral, que recebe só o id
        da célula — ao contrário do gráfico do dashboard, que já sabe o site.
        """
        wanted = str(cell_id or "").upper()
        if not wanted:
            return None
        for site in merged:
            for cell in site.get("cells") or []:
                cell_key = cell if isinstance(cell, str) else cell.get("id", "")
                if str(cell_key).upper() == wanted:
                    return site
        return None

    def _owner_site_id_for_cell(self, site: dict | None, cell_id: str, fallback: str) -> str:
        if not site or not cell_id:
            return fallback
        wanted = str(cell_id).upper()
        for member in site.get("members") or []:
            if str(member.get("site_id", "")).upper() == wanted:
                return member["site_id"]
        for cell in site.get("cells") or []:
            cell_key = cell if isinstance(cell, str) else cell.get("id", "")
            if str(cell_key).upper() != wanted:
                continue
            family = None if isinstance(cell, str) else cell.get("family")
            family = family or self._cell_technology_family(cell)
            for member in site.get("members") or []:
                if member.get("family") in (family, None):
                    return member["site_id"]
            break
        members = site.get("members") or []
        return members[0]["site_id"] if members else fallback

    def _site_series_by_family(
            self, event_id: str, member_ids: list[str], metric: str, minutes: int,
            technology: str | None, technology_family: str | None) -> list[dict]:
        by_family: dict[str, dict] = {}
        for member_id in member_ids:
            rows = db.get_kpi_site_series(event_id, member_id, metric, minutes, technology)
            for row in rows:
                family = self._technology_family(row.get("technology"))
                if technology_family and family and family != technology_family:
                    continue
                family = family or technology_family or "unknown"
                bucket = by_family.setdefault(family, {})
                bucket.setdefault(row["timestamp"], []).append(row["value"])
        series = []
        for family in sorted(by_family, key=lambda item: (item == "unknown", item)):
            ts_map = by_family[family]
            labels = sorted(ts_map)
            values = [self._combine_family_values(metric, ts_map[ts]) for ts in labels]
            series.append({
                "technology": None if family == "unknown" else family,
                "labels": labels,
                "values": values,
            })
        return series

    def _cluster_series_by_family(
            self, event_id: str, selections: list[dict], metric: str, minutes: int,
            technology: str | None, technology_family: str | None) -> list[dict]:
        """Monta séries de cluster respeitando sites inteiros e células parciais."""
        by_family: dict[str, dict] = {}
        for selection in selections:
            site_id = selection["site_id"]
            selected_cells = selection.get("cell_ids")
            per_site: dict[str, dict] = {}
            if selected_cells is None:
                rows = db.get_kpi_site_series(
                    event_id, site_id, metric, minutes, technology)
                for row in rows:
                    family = self._technology_family(row.get("technology"))
                    if technology_family and family and family != technology_family:
                        continue
                    family = family or technology_family or "unknown"
                    per_site.setdefault(family, {}).setdefault(
                        row["timestamp"], []).append(row["value"])
            else:
                wanted = {str(cell_id) for cell_id in selected_cells}
                rows = self._collect_cell_rows(event_id, [site_id], metric, minutes)
                for row in rows:
                    if str(row.get("cell_id")) not in wanted or row.get("value") is None:
                        continue
                    family = self._cell_technology_family(row.get("cell_id"))
                    if technology_family and family and family != technology_family:
                        continue
                    family = family or technology_family or "unknown"
                    per_site.setdefault(family, {}).setdefault(
                        row["timestamp"], []).append(row["value"])

            # Uma seleção parcial vira primeiro um agregado do seu próprio site;
            # depois os sites são combinados com a mesma regra da série legada.
            for family, timestamps in per_site.items():
                target = by_family.setdefault(family, {})
                for timestamp, values in timestamps.items():
                    value = self._combine_family_values(metric, values)
                    if value is not None:
                        target.setdefault(timestamp, []).append(value)

        series = []
        for family in sorted(by_family, key=lambda item: (item == "unknown", item)):
            timestamps = by_family[family]
            labels = sorted(timestamps)
            series.append({
                "technology": None if family == "unknown" else family,
                "labels": labels,
                "values": [
                    self._combine_family_values(metric, timestamps[timestamp])
                    for timestamp in labels
                ],
            })
        return series

    def _collect_cell_rows(self, event_id: str, member_ids: list[str],
                           metric: str, minutes: int) -> list:
        rows = []
        for member_id in member_ids:
            if metric == "utilization":
                raw = db.get_kpi_series(event_id, member_id, "utilization", minutes)
                if not raw:
                    combined = {}
                    for r in db.get_kpi_series(event_id, member_id, "utilization_dl", minutes):
                        combined[(r["cell_id"], r["timestamp"])] = r["value"]
                    for r in db.get_kpi_series(event_id, member_id, "utilization_ul", minutes):
                        key = (r["cell_id"], r["timestamp"])
                        combined[key] = max(combined[key], r["value"]) if key in combined else r["value"]
                    raw = [
                        {"cell_id": cell, "timestamp": ts, "value": val, "site_id": member_id}
                        for (cell, ts), val in combined.items()
                    ]
                rows.extend(raw)
            else:
                rows.extend(db.get_kpi_series(event_id, member_id, metric, minutes))
        return rows

    def _cells_data_for_rows(self, rows: list, labels: list) -> dict:
        cell_ids = sorted({r["cell_id"] for r in rows if r.get("cell_id")})
        cell_ts_vals = {
            (r["cell_id"], r["timestamp"]): r["value"]
            for r in rows if r.get("cell_id") and r.get("timestamp")
        }
        return {cid: [cell_ts_vals.get((cid, ts)) for ts in labels] for cid in cell_ids}

    def _filter_cell_rows_for_family(self, rows: list, family: str | None) -> list:
        if not family:
            return list(rows)
        return [
            row for row in rows
            if self._cell_technology_family(row.get("cell_id")) in (family, None)
        ]

    def _average_series_by_family(self, rows: list) -> list[dict]:
        by_family: dict[str, dict] = {}
        for row in rows:
            if row.get("value") is None:
                continue
            family = self._cell_technology_family(row.get("cell_id")) or "unknown"
            by_family.setdefault(family, {}).setdefault(row["timestamp"], []).append(row["value"])
        series = []
        for family in sorted(by_family, key=lambda item: (item == "unknown", item)):
            ts_map = by_family[family]
            labels = sorted(ts_map)
            values = [sum(ts_map[ts]) / len(ts_map[ts]) for ts in labels]
            series.append({
                "technology": None if family == "unknown" else family,
                "labels": labels,
                "values": values,
            })
        return series

    def _kpi_series_for_one_site(self, event_id: str, site_id: str, metric: str,
                                 minutes: int, cell_id: str, technology: str | None) -> dict:
        if cell_id == "__all__":
            site_rows = db.get_kpi_site_series(event_id, site_id, metric, minutes, technology)
            if site_rows:
                labels = [row["timestamp"] for row in site_rows]
                cell_rows = db.get_kpi_series(event_id, site_id, metric, minutes)
                return {
                    "ok": True, "labels": labels, "values": [row["value"] for row in site_rows],
                    "cells_data": self._cells_data_for_rows(cell_rows, labels),
                    "gaps": self._detect_gaps(labels, max_gap_seconds=90),
                    "technology": technology, "persisted_site_aggregate": True,
                    "thresholds": self._metric_thresholds(event_id, metric),
                }
        rows = self._collect_cell_rows(event_id, [site_id], metric, minutes)
        if cell_id and cell_id not in ("__all__", "__media__"):
            rows = [r for r in rows if r.get("cell_id") == cell_id]

        ts_groups = {}
        for r in rows:
            ts = r["timestamp"]
            val = r["value"]
            if val is not None:
                ts_groups.setdefault(ts, []).append(val)

        aggregated = []
        for ts, vals in sorted(ts_groups.items()):
            if not vals:
                continue
            if cell_id == "__media__":
                val = sum(vals) / len(vals)
            elif "availability" in metric or "accessibility" in metric:
                val = sum(vals) / len(vals)
            elif "throughput" in metric:
                val = sum(vals)
            elif "rsrp" in metric or "rsrq" in metric:
                val = sum(vals) / len(vals)
            else:
                val = max(vals)
            aggregated.append({"timestamp": ts, "value": val})

        labels = [r["timestamp"] for r in aggregated]
        values = [r["value"] for r in aggregated]
        cells_data = self._cells_data_for_rows(rows, labels) if cell_id == "__all__" else {}
        return {
            "ok": True,
            "labels": labels,
            "values": values,
            "cells_data": cells_data,
            "gaps": self._detect_gaps(labels, max_gap_seconds=90),
            "thresholds": self._metric_thresholds(event_id, metric),
        }

    def get_kpi_series(self, event_id: str, site_id: str, metric: str,
                       minutes: int = 60, cell_id: str = "__all__",
                       technology: str = None,
                       technology_family: str = None,
                       scope: str = None, scope_id: str = None) -> dict:
        """Retorna série temporal para o gráfico de KPIs.

        Aceita o id fundido: expande para os ``site_id`` dos membros e devolve
        ``series`` com uma entrada por família. Com uma família só, ``labels``/
        ``values`` continuam no formato antigo.

        ``scope="cluster"`` combina linhas SITE para membros completos. Quando o
        cluster contém uma seleção parcial, somente as linhas CELL explicitamente
        escolhidas entram no agregado. ``scope="cell"`` acha o site dono de
        ``scope_id`` no evento inteiro e cai no caminho de célula única.
        ``scope="site_carrier"`` recorta um site por portadora — ``scope_id`` é
        ``<site_id>::<earfcn>`` — e reaproveita a mesma agregação do cluster.
        """
        try:
            config = db.get_event(event_id) or _active_event
            merged = self._merged_sites(config) if config else []

            if scope == "cell":
                owner = self._find_site_for_cell(merged, scope_id)
                if not owner:
                    return {
                        "ok": True, "labels": [], "values": [], "series": [],
                        "cells_data": {}, "gaps": [],
                        "thresholds": self._metric_thresholds(event_id, metric),
                    }
                site_id = owner["id"]
                cell_id = scope_id

            if scope == "cluster":
                family = technology_family if technology_family in ("4G", "5G") else None
                thresholds = self._metric_thresholds(event_id, metric)
                cluster = self._find_cluster(config, scope_id)
                if not cluster:
                    return {
                        "ok": True, "labels": [], "values": [], "series": [],
                        "cells_data": {}, "gaps": [], "thresholds": thresholds,
                    }
                selections = self._cluster_raw_selections(cluster, merged, config)
                series = self._cluster_series_by_family(
                    event_id, selections, metric, minutes, technology, family)
                if not series:
                    return {
                        "ok": True, "labels": [], "values": [], "series": [],
                        "cells_data": {}, "gaps": [], "thresholds": thresholds,
                    }
                aligned, common = self._align_kpi_series(series)
                axis = aligned[0]["labels"] if len(aligned) == 1 else common
                return {
                    "ok": True,
                    "labels": axis,
                    "values": aligned[0]["values"] if len(aligned) == 1 else [],
                    "series": aligned,
                    "cells_data": {},
                    "gaps": self._detect_gaps(axis, max_gap_seconds=90),
                    "thresholds": thresholds,
                }

            if scope == "site_carrier":
                family = technology_family if technology_family in ("4G", "5G") else None
                thresholds = self._metric_thresholds(event_id, metric)
                parts = (scope_id or "").rsplit("::", 1)
                selections = (
                    self._site_carrier_selections(config, merged, parts[0], parts[1], family)
                    if len(parts) == 2 and parts[0] and parts[1] else []
                )
                series = self._cluster_series_by_family(
                    event_id, selections, metric, minutes, technology, family)
                if not series:
                    return {
                        "ok": True, "labels": [], "values": [], "series": [],
                        "cells_data": {}, "gaps": [], "thresholds": thresholds,
                    }
                aligned, common = self._align_kpi_series(series)
                axis = aligned[0]["labels"] if len(aligned) == 1 else common
                return {
                    "ok": True,
                    "labels": axis,
                    "values": aligned[0]["values"] if len(aligned) == 1 else [],
                    "series": aligned,
                    "cells_data": {},
                    "gaps": self._detect_gaps(axis, max_gap_seconds=90),
                    "thresholds": thresholds,
                }

            site = self._find_merged_site(merged, site_id)
            members = list((site or {}).get("members") or [{"site_id": site_id, "family": None}])
            family = technology_family if technology_family in ("4G", "5G") else None
            if family:
                filtered = [m for m in members if m.get("family") in (family, None)]
                if filtered:
                    members = filtered

            if cell_id and cell_id not in ("__all__", "__media__"):
                owner = self._owner_site_id_for_cell(site, cell_id, members[0]["site_id"])
                result = self._kpi_series_for_one_site(
                    event_id, owner, metric, minutes, cell_id, technology)
                if result.get("ok"):
                    cell_family = family
                    if site:
                        for cell in site.get("cells") or []:
                            cid = cell if isinstance(cell, str) else cell.get("id")
                            if cid == cell_id:
                                cell_family = (
                                    None if isinstance(cell, str) else cell.get("family")
                                ) or self._cell_technology_family(cell) or family
                                break
                    result["series"] = [{
                        "technology": cell_family,
                        "labels": result.get("labels") or [],
                        "values": result.get("values") or [],
                    }]
                return result

            member_ids = self._member_ids_for_family(members, family) or [site_id]
            cell_rows = self._filter_cell_rows_for_family(
                self._collect_cell_rows(event_id, member_ids, metric, minutes), family)
            thresholds = self._metric_thresholds(event_id, metric)

            if cell_id == "__media__":
                series = self._average_series_by_family(cell_rows)
                if not series:
                    series = self._site_series_by_family(
                        event_id, member_ids, metric, minutes, technology, family)
                if not series:
                    return {
                        "ok": True, "labels": [], "values": [], "series": [],
                        "cells_data": {}, "gaps": [], "thresholds": thresholds,
                    }
                aligned, common = self._align_kpi_series(series)
                axis = aligned[0]["labels"] if len(aligned) == 1 else common
                return {
                    "ok": True,
                    "labels": axis,
                    "values": aligned[0]["values"] if len(aligned) == 1 else [],
                    "series": aligned,
                    "cells_data": {},
                    "gaps": self._detect_gaps(axis, max_gap_seconds=90),
                    "thresholds": thresholds,
                }

            site_series = self._site_series_by_family(
                event_id, member_ids, metric, minutes, technology, family)
            cell_labels = sorted({row["timestamp"] for row in cell_rows if row.get("timestamp")})
            if site_series:
                aligned, common = self._align_kpi_series(site_series)
            else:
                aligned, common = [], cell_labels
            axis = cell_labels or common
            cells_data = self._cells_data_for_rows(cell_rows, axis) if axis else {}
            if not axis and not aligned:
                result = self._kpi_series_for_one_site(
                    event_id, member_ids[0], metric, minutes, cell_id, technology)
                if result.get("ok") and not result.get("series"):
                    fam = family or (members[0].get("family") if members else None)
                    result["series"] = [{
                        "technology": fam,
                        "labels": result.get("labels") or [],
                        "values": result.get("values") or [],
                    }]
                return result
            return {
                "ok": True,
                "labels": axis,
                "values": aligned[0]["values"] if len(aligned) == 1 else [],
                "series": aligned,
                "cells_data": cells_data,
                "gaps": self._detect_gaps(axis, max_gap_seconds=90),
                "technology": technology,
                "persisted_site_aggregate": bool(aligned),
                "thresholds": thresholds,
            }
        except Exception as e:
            logger.error(f"get_kpi_series error: {e}")
            return {"ok": False, "labels": [], "values": [], "series": [], "gaps": []}

    def get_kpi_overview(self, event_id: str, scope: str, scope_id: str,
                         technology_family: str, minutes: int = 60) -> dict:
        """Retorna todos os KPIs da visao geral sobre uma unica grade temporal.

        O endpoint reutiliza exatamente a mesma expansao de site fundido e a
        mesma agregacao de cluster de :meth:`get_kpi_series`. A uniao dos
        timestamps e feita somente depois de calcular todas as metricas; assim,
        um ponto ausente vira ``None`` na posicao correta em vez de deslocar a
        serie e quebrar a sincronizacao dos graficos.
        """
        family = technology_family if technology_family in KPI_OVERVIEW_METRICS else None
        normalized_scope = scope if scope in ("site", "cluster", "cell", "site_carrier") else None
        if not family:
            return {
                "ok": False, "error": "technology_family deve ser 4G ou 5G",
                "labels": [], "metrics": {}, "units": {}, "thresholds": {},
                "reasons": {},
            }
        if not normalized_scope or not scope_id:
            return {
                "ok": False,
                "error": "scope deve ser site, cluster, cell ou site_carrier e possuir scope_id",
                "labels": [], "metrics": {}, "units": {}, "thresholds": {},
                "reasons": {},
            }

        try:
            metric_ids = KPI_OVERVIEW_METRICS[family]
            raw_metrics: dict[str, dict] = {}
            common_labels: set[str] = set()

            for metric in metric_ids:
                result = self.get_kpi_series(
                    event_id,
                    scope_id if normalized_scope == "site" else None,
                    metric,
                    minutes,
                    "__all__",
                    None,
                    family,
                    normalized_scope if normalized_scope in ("cluster", "cell", "site_carrier")
                    else None,
                    scope_id if normalized_scope in ("cluster", "cell", "site_carrier")
                    else None,
                )
                if not result.get("ok", False):
                    raise RuntimeError(
                        result.get("error") or f"falha ao consultar a metrica {metric}")

                series = result.get("series") or []
                selected = next(
                    (item for item in series if item.get("technology") in (family, None)),
                    series[0] if series else None,
                )
                labels = list((selected or {}).get("labels") or result.get("labels") or [])
                values = list((selected or {}).get("values") or result.get("values") or [])
                lookup = dict(zip(labels, values))
                raw_metrics[metric] = lookup
                common_labels.update(labels)

            labels = sorted(common_labels)
            metrics = {
                metric: [raw_metrics[metric].get(timestamp) for timestamp in labels]
                for metric in metric_ids
            }

            catalog = catalog_for_api()
            family_catalog = [
                item for item in catalog
                if (item.get("technology") == "4G") == (family == "4G")
            ]
            units = {
                metric: next(
                    (item.get("unit") for item in family_catalog
                     if item.get("id") == metric),
                    "",
                )
                for metric in metric_ids
            }
            thresholds = {
                metric: self._metric_thresholds(event_id, metric)
                for metric in metric_ids
            }
            return {
                "ok": True,
                "scope": normalized_scope,
                "scope_id": scope_id,
                "technology_family": family,
                "labels": labels,
                "metrics": metrics,
                "units": units,
                "thresholds": thresholds,
                "reasons": _overview_reasons(labels, metrics),
            }
        except Exception as e:
            logger.error(f"get_kpi_overview error: {e}")
            return {
                "ok": False, "error": str(e), "labels": [], "metrics": {},
                "units": {}, "thresholds": {}, "reasons": {},
            }

    def get_kpi_overview_multi(self, event_id: str, scopes: list,
                               technology_family: str, minutes: int = 60) -> dict:
        """Compara varios escopos (clusters, sites e/ou celulas) na mesma grade temporal.

        Cada escopo reutiliza :meth:`get_kpi_overview`, entao a expansao de site
        fundido e a agregacao de cluster continuam saindo de um unico lugar. A
        uniao dos timestamps so acontece depois que todos os escopos voltaram:
        um escopo sem ponto num minuto vira ``None`` naquela posicao em vez de
        deslocar a serie e quebrar a sincronizacao dos nove graficos.
        """
        family = technology_family if technology_family in KPI_OVERVIEW_METRICS else None
        if not family:
            return {
                "ok": False, "error": "technology_family deve ser 4G ou 5G",
                "labels": [], "series": [], "units": {}, "thresholds": {},
                "reasons": {},
            }

        requested: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for entry in scopes or []:
            if not isinstance(entry, dict):
                continue
            scope = entry.get("scope")
            scope_id = entry.get("scope_id") or entry.get("id")
            if scope not in ("site", "cluster", "cell", "site_carrier") or not scope_id:
                continue
            key = (scope, str(scope_id))
            if key not in seen:
                seen.add(key)
                requested.append(key)

        if not requested:
            return {
                "ok": False, "error": "informe ao menos um cluster, site ou célula",
                "labels": [], "series": [], "units": {}, "thresholds": {},
                "reasons": {},
            }
        if len(requested) > KPI_OVERVIEW_MAX_SCOPES:
            return {
                "ok": False,
                "error": (f"compare no maximo {KPI_OVERVIEW_MAX_SCOPES} escopos "
                          f"por vez (foram pedidos {len(requested)})"),
                "labels": [], "series": [], "units": {}, "thresholds": {},
                "reasons": {},
            }

        try:
            metric_ids = KPI_OVERVIEW_METRICS[family]
            collected: list[dict] = []
            failures: list[dict] = []
            label_union: set[str] = set()
            units: dict = {}
            thresholds: dict = {}

            for scope, scope_id in requested:
                result = self.get_kpi_overview(
                    event_id, scope, scope_id, family, minutes)
                if not result.get("ok"):
                    failures.append({
                        "scope": scope, "scope_id": scope_id,
                        "error": result.get("error") or "falha ao consultar o escopo",
                    })
                    continue
                labels = list(result.get("labels") or [])
                result_metrics = result.get("metrics") or {}
                collected.append({
                    "scope": scope,
                    "scope_id": scope_id,
                    "reasons": result.get("reasons") or {},
                    "lookup": {
                        metric: dict(zip(labels, result_metrics.get(metric) or []))
                        for metric in metric_ids
                    },
                })
                label_union.update(labels)
                units = units or result.get("units") or {}
                thresholds = thresholds or result.get("thresholds") or {}

            if not collected:
                return {
                    "ok": False,
                    "error": failures[0]["error"] if failures else "sem dados no periodo",
                    "labels": [], "series": [], "units": {}, "thresholds": {},
                    "reasons": {}, "failures": failures,
                }

            labels = sorted(label_union)
            # Um escopo com trafego basta para o painel nao estar vazio; so
            # quando nenhum deles tem ponto e que a frase da tela muda.
            reasons = {
                metric: min(
                    (item["reasons"].get(metric, "no_data") for item in collected),
                    key=_REASON_RANK.get,
                )
                for metric in metric_ids
            }
            series = [{
                "scope": item["scope"],
                "scope_id": item["scope_id"],
                "metrics": {
                    metric: [item["lookup"][metric].get(timestamp) for timestamp in labels]
                    for metric in metric_ids
                },
            } for item in collected]
            return {
                "ok": True,
                "technology_family": family,
                "labels": labels,
                "series": series,
                "units": units,
                "thresholds": thresholds,
                "reasons": reasons,
                "failures": failures,
            }
        except Exception as e:
            logger.error(f"get_kpi_overview_multi error: {e}")
            return {
                "ok": False, "error": str(e),
                "labels": [], "series": [], "units": {}, "thresholds": {},
                "reasons": {},
            }

    def _metric_thresholds(self, event_id: str, metric: str) -> dict:
        """B2: devolve sempre o objeto normalizado ``{"value", "unit"}``.

        Aceita o threshold gravado no formato legado (número cru) ou novo — ver
        ``core.kpi_formulas.threshold_object``. Os KPIs alcançáveis por aqui são
        percentuais, daí o ``unit_default="%"`` para thresholds legados sem unidade.
        """
        config = db.get_event(event_id) or _active_event or {}
        thresholds = config.get("thresholds", {})
        warning = thresholds.get(f"{metric}_warning")
        if warning is None and "utilization" in metric:
            warning = thresholds.get("utilization_warning")
        critical = thresholds.get(f"{metric}_critical")
        if critical is None and "utilization" in metric:
            critical = thresholds.get("utilization_critical")
        return {
            "warning": threshold_object(warning, "%"),
            "critical": threshold_object(critical, "%"),
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
            rsrp_warn = threshold_value(thresholds.get("rsrp_warning"), -100)
            rsrp_crit = threshold_value(thresholds.get("rsrp_critical"), -110)

            merged_sites = self._merged_sites(config)
            resolve_site_id = self._create_cell_resolver(merged_sites)

            site_id_to_name = {site["id"]: site["name"] for site in merged_sites}

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
                    "rsrp_min":          threshold_value(thresholds.get("rsrp_warning")),
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
            sites = self._merged_sites(config) if config else []
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
            sites = self._merged_sites(config)
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
    def _site_identity_keys(site: dict) -> list[str]:
        keys = [site.get("id", ""), site.get("name", "")]
        for member in site.get("members") or []:
            keys.append(member.get("site_id") or "")
        return [key for key in keys if key]

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
            for member in site.get("members") or []:
                mid = member.get("site_id")
                if mid:
                    cell_to_site[str(mid).upper()] = site_id

        def resolve_site_id(cell_id):
            if not cell_id:
                return None
            cell_id_str = str(cell_id).strip()
            cell_id_upper = cell_id_str.upper()

            # 1. Match exato com célula/obj_no/membro mapeado
            if cell_id_upper in cell_to_site:
                return cell_to_site[cell_id_upper]

            # 2. Match por prefixo ou contendo no id fundido, nome ou membros
            for site in sites:
                s_id = site["id"]
                s_name_upper = site.get("name", "").upper()
                for key in Api._site_identity_keys(site):
                    key_upper = key.upper()
                    id_matches = (
                        cell_id_upper.startswith(key_upper)
                        or (len(key_upper) >= 4 and key_upper in cell_id_upper)
                    )
                    if id_matches:
                        return s_id
                name_matches = len(s_name_upper) >= 4 and (
                    s_name_upper in cell_id_upper
                    or (len(cell_id_upper) >= 4 and cell_id_upper in s_name_upper)
                )
                if name_matches:
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
                                for key in Api._site_identity_keys(site):
                                    if inferred_str in key.upper():
                                        return site["id"]
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
        Match por igualdade/prefixo/substring contra o id fundido, o nome e os membros."""
        if not source:
            return None, None
        src = str(source).strip().upper()
        if not src:
            return None, None
        for site in sites:
            for key in Api._site_identity_keys(site):
                key_u = key.upper()
                if src == key_u or src.startswith(key_u) or key_u in src:
                    return site.get("id"), site.get("name")
            s_name_u = (site.get("name") or "").upper()
            if s_name_u and (s_name_u in src or src in s_name_u):
                return site.get("id"), site.get("name")
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

    @staticmethod
    def _alert_is_cell_level(alert: dict) -> bool:
        message = str(alert.get("message") or "").lower()
        if "célula" in message or "celula" in message:
            return True
        if "rsrp" in message:
            return True
        site_id = str(alert.get("site_id") or "")
        cell_id = str(alert.get("cell_id") or "")
        return bool(cell_id) and cell_id == site_id and not site_id.isdigit()

    def _enrich_alerts(self, event_id: str, alerts: list) -> list:
        """Resolve enodebID cru para nome de site/célula e o site fundido dono.

        O banco guarda o id técnico da medição. A tela e o log de download
        mostram o nome: site nas alertas de utilização, célula nas de
        acessibilidade e de RSRP do VIP.
        """
        config = db.get_event(event_id) or _active_event or {}
        merged = self._merged_sites(config) if config else []
        resolve = self._create_cell_resolver(merged)
        for alert in alerts:
            site_id = str(alert.get("site_id") or "")
            cell_id = str(alert.get("cell_id") or "")
            site = self._find_merged_site(merged, site_id)
            if not site:
                resolved = resolve(site_id) or resolve(cell_id)
                site = self._find_merged_site(merged, resolved) if resolved else None
            site_name = (site or {}).get("name") or None
            serving = (site or {}).get("id")
            cell_name = cell_id or None
            if self._alert_is_cell_level(alert):
                display = cell_name or site_name or site_id
            else:
                display = site_name or site_id
            alert["site_name"] = site_name
            alert["cell_name"] = cell_name
            alert["display_name"] = display or site_id or "GLOBAL"
            alert["serving_site"] = serving
            message = alert.get("message") or ""
            if site_id and site_name and site_id != site_name and site_id.isdigit():
                message = message.replace(site_id, site_name)
                alert["message"] = message
        return alerts

    def get_alerts(self, event_id: str, timestamp: Optional[str] = None) -> list:
        try:
            return self._enrich_alerts(event_id, db.get_active_alerts(event_id, timestamp))
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
            alerts = self._enrich_alerts(event_id, db.get_all_alerts(event_id))
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
                    site = a.get("display_name") or a.get("site_name") or a.get("site_id") or "GLOBAL"
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

