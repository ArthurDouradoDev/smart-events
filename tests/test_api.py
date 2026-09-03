"""
Testes para api/api.py.

Usa o banco temporário via fixture tmp_db e monkeypatcha o scheduler
para não subir threads reais.
"""
import copy
import time
from types import SimpleNamespace

import pytest
import core.database as database
from api.api import Api
import api.api as api_module


@pytest.fixture
def api(tmp_db, monkeypatch):
    """Api com banco temporário e scheduler mockado."""
    from core import scheduler as sched_module

    # is_recording é uma @property que lê _recording — patchamos o atributo interno
    monkeypatch.setattr(sched_module.scheduler, "_recording", False)
    monkeypatch.setattr(sched_module.scheduler, "start", lambda *a, **k: None)
    monkeypatch.setattr(sched_module.scheduler, "stop", lambda *a, **k: None)
    monkeypatch.setattr(sched_module.scheduler, "set_update_callback", lambda *a, **k: None)
    monkeypatch.setattr(sched_module.scheduler, "get_status",
                        lambda: {"kpi": {"state": "idle"}, "vip": {"state": "idle"}})
    monkeypatch.setattr(api_module, "_active_event", None)
    return Api()


@pytest.fixture
def api_with_event(api, sample_event):
    database.save_event(sample_event)
    return api, sample_event


class TestApiEvents:
    def test_get_events_empty(self, api):
        result = api.get_events()
        assert isinstance(result, list)

    def test_get_events_returns_saved(self, api_with_event):
        api, ev = api_with_event
        events = api.get_events()
        assert any(e["id"] == ev["id"] for e in events)

    def test_activate_event(self, api_with_event):
        api, ev = api_with_event
        result = api.activate_event(ev["id"], mock=True)
        assert result.get("ok") is True

    def test_activate_event_preserva_outros_eventos_ativos(self, api, sample_event):
        first = {**sample_event, "id": "event-a", "name": "Evento A"}
        second = {**sample_event, "id": "event-b", "name": "Evento B"}
        database.save_event(first)
        database.save_event(second)
        database.update_event_status(first["id"], "ACTIVE")

        result = api.activate_event(second["id"], mock=True)

        assert result["ok"] is True
        assert database.get_event(first["id"])["status"] == "ACTIVE"
        assert database.get_event(second["id"])["status"] == "ACTIVE"
        assert {row["id"] for row in database.get_events(status="ACTIVE")} == {
            first["id"], second["id"]
        }

    def test_end_event(self, api_with_event):
        api, ev = api_with_event
        result = api.end_event(ev["id"])
        assert result.get("ok") is True

    def test_activate_nonexistent_event(self, api):
        result = api.activate_event("nao-existe", mock=True)
        assert result.get("ok") is False

    def test_get_sites_preserva_ultimo_resultado_quando_banco_bloqueia(
            self, api_with_event, monkeypatch):
        api, event = api_with_event
        first = api.get_sites(event["id"])
        assert first

        def locked(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(database, "get_event", locked)
        assert api.get_sites(event["id"]) == first

    def test_evento_com_um_monitoring_5g_esconde_sites_4g_e_preserva_desconhecidos(
            self, api, sample_event):
        event = {
            **sample_event,
            "id": "sites-only-nrducell",
            "integration": {"pm_tasks": [{"task_id": 748, "tech": "NRDUCELL"}]},
            "sites": [
                {"id": "LTE", "name": "LTE", "lat": 0, "lng": 0,
                 "cells": [{"id": "4G-SITE-18-A"}]},
                {"id": "NR", "name": "NR", "lat": 0, "lng": 0,
                 "cells": [{"id": "4G-MIXED-18-A"}, {"id": "5G-MIXED-35-A"}]},
                {"id": "UNKNOWN", "name": "UNKNOWN", "lat": 0, "lng": 0,
                 "cells": [{"id": "18NLCTAL01GI"}]},
            ],
        }
        database.save_event(event)

        sites = api.get_sites(event["id"])

        assert {site["id"] for site in sites} == {"NR", "UNKNOWN"}
        assert [cell["id"] for cell in api.get_site_cells(event["id"], "NR")] == [
            "5G-MIXED-35-A"
        ]

    def test_reauth_session_reagenda_headless_sem_abrir_processo_manual(
            self, api_with_event, monkeypatch):
        api, event = api_with_event
        api_module._active_event = event
        invalidated = []

        class Collector:
            def _invalidate_session(self, module):
                invalidated.append(module)

        monkeypatch.setattr(api_module.scheduler, "_collector", Collector())
        monkeypatch.setattr(
            api_module.credentials,
            "resolve_base_url",
            lambda _oss: "https://10.220.30.9:31943",
        )
        monkeypatch.setattr(
            "api.api.subprocess.run",
            lambda *_args, **_kwargs: pytest.fail("navegador/processo manual não pode abrir"),
        )

        result = api.reauth_session()

        assert result["ok"] is True
        assert result["automatic"] is True
        assert invalidated == ["monitoring", "trace"]


class TestApiVpn:
    def test_without_active_event_is_indeterminate(self, api):
        result = api.check_vpn()

        assert result == {
            "ok": True,
            "connected": None,
            "target": None,
            "reason": "no_active_event",
        }

    def test_uses_ping_exit_code(self, api_with_event, monkeypatch):
        api, event = api_with_event
        api_module._active_event = event
        monkeypatch.setattr(
            api_module.credentials,
            "resolve_base_url",
            lambda _oss: "https://10.220.30.9:31943",
        )
        monkeypatch.setattr(
            api_module.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=b"", stderr=b""
            ),
        )

        result = api.check_vpn()

        assert result == {
            "ok": True,
            "connected": True,
            "target": "10.220.30.9",
        }


class TestApiVips:
    def test_create_vip(self, api):
        result = api.create_vip("Teste Vip")
        assert result["ok"] is True
        assert result["vip"]["name"] == "Teste Vip"

    def test_create_vip_duplicate_name(self, api):
        api.create_vip("Fulano")
        result2 = api.create_vip("Fulano")
        assert "ok" in result2

    def test_delete_vip(self, api):
        r = api.create_vip("Para Deletar")
        vip_id = r["vip"]["id"]
        del_result = api.delete_vip(vip_id)
        assert del_result.get("ok") is True

    def test_delete_nonexistent_vip(self, api):
        result = api.delete_vip("nao-existe-id")
        assert result.get("ok") is False

    def test_get_vips_empty(self, api):
        result = api.get_vips(event_id=None)
        assert isinstance(result, (list, dict))

    def test_assign_vip_and_find_by_region(self, api_with_event):
        """
        get_event_vips retorna VIPs do banco filtrados por oss=SP (região do evento).
        Criamos o VIP diretamente com oss="SP" para garantir que apareça.
        """
        api, ev = api_with_event
        # Cria VIP via banco (oss="SP") para aparecer no filtro regional
        vip = database.save_vip({"name": "VIP SP", "oss": "SP"})
        ev_vips = api.get_event_vips(ev["id"])
        assert any(v["name"] == "VIP SP" for v in ev_vips)


class TestApiSettings:
    def test_get_settings_returns_ok(self, api):
        # get_settings retorna {"ok": True, "settings": {...}}
        result = api.get_settings()
        assert result.get("ok") is True
        assert "settings" in result

    def test_save_and_get_settings(self, api):
        # save_settings persiste via db; get_settings lê do banco
        database.save_settings({"theme": "dark"})
        result = api.get_settings()
        assert result["settings"].get("theme") == "dark"

    def test_save_settings_returns_ok(self, api):
        result = api.save_settings({"lang": "pt-BR"})
        assert result.get("ok") is True


class TestApiKpiCatalog:
    def test_evento_legado_com_uma_task_expoe_somente_kpis_4g(
            self, api, sample_event):
        event = {
            **sample_event,
            "id": "legacy-4g",
            "integration": {"pm_task_id": 2225},
        }
        database.save_event(event)

        result = api.get_kpi_catalog(event["id"])

        assert result["ok"] is True
        assert result["technologies"] == ["4G"]
        assert result["metrics"]
        assert {item["technology"] for item in result["metrics"]} == {"4G"}

    def test_pm_tasks_filtra_catalogo_pelas_tecnologias_configuradas(
            self, api, sample_event):
        event = {
            **sample_event,
            "id": "only-5g",
            "integration": {
                "pm_tasks": [{"task_id": 2241, "tech": "NRCELL"}],
            },
        }
        database.save_event(event)

        result = api.get_kpi_catalog(event["id"])

        assert result["technologies"] == ["5G_NRCELL"]
        assert result["metrics"]
        assert {item["technology"] for item in result["metrics"]} == {"5G_NRCELL"}
        assert {item["id"] for item in result["metrics"]} == {
            "accessibility", "drop_rate", "availability", "user_count",
        }

    def test_nrducell_expoe_somente_kpis_da_task_748(
            self, api, sample_event):
        event = {
            **sample_event,
            "id": "catalogo-nrducell",
            "integration": {
                "pm_tasks": [{"task_id": 748, "tech": "NRDUCELL"}],
            },
        }
        database.save_event(event)

        result = api.get_kpi_catalog(event["id"])

        assert result["technologies"] == ["5G_NRDUCELL"]
        assert {item["technology"] for item in result["metrics"]} == {"5G_NRDUCELL"}
        assert {item["id"] for item in result["metrics"]} == {
            "utilization_dl", "utilization_ul", "throughput_dl", "throughput_ul",
            "traffic_volume_dl_sa", "traffic_volume_dl_nsa",
            "traffic_volume_ul_sa", "traffic_volume_ul_nsa", "interference_ul",
        }
        throughput_dl = next(item for item in result["metrics"] if item["id"] == "throughput_dl")
        assert throughput_dl["production_ready"] is True
        # Fase 3: o catálogo anuncia a unidade canônica; a do OSS fica ao lado.
        assert throughput_dl["unit"] == "bit/s"
        assert throughput_dl["oss_unit"] == "Mbit/s"

    def test_evento_sem_task_nao_anuncia_kpi_indisponivel(
            self, api, sample_event):
        event = {**sample_event, "id": "without-monitoring", "integration": {}}
        database.save_event(event)

        result = api.get_kpi_catalog(event["id"])

        assert result == {"ok": True, "metrics": [], "technologies": []}


class TestApiCollectionStatus:
    def test_get_collection_status_structure(self, api):
        status = api.get_collection_status()
        assert status.get("ok") is True
        assert "recording" in status
        assert "kpi" in status
        assert "vip" in status
        assert "overall_state" in status
        assert {"region", "host", "fars_contract"} <= set(status["session"])
        assert "task_causes" in status["vip"]

    def test_collection_status_ok_is_local_envelope_not_health(self, api, monkeypatch):
        from core import scheduler as sched_module
        monkeypatch.setattr(sched_module.scheduler, "get_status", lambda: {
            "kpi": {"state": "error", "error": "HTTP 500"},
            "vip": {"state": "data"},
            "alarms": {"state": "empty"},
        })
        status = api.get_collection_status()
        assert status["ok"] is True
        assert status["overall_state"] == "error"

    def test_collection_status_not_recording_by_default(self, api):
        status = api.get_collection_status()
        assert status["recording"] is False

    def test_get_app_status_has_db_size(self, api):
        # db_size_mb está em get_app_status(), não em get_collection_status()
        status = api.get_app_status()
        assert "db_size_mb" in status
        assert "recording" in status


class TestApiVipSeries:
    @pytest.fixture
    def mock_db_series(self, monkeypatch):
        rows = [
            {"timestamp": "2026-08-12T10:00:00Z", "serving_cell": "CELL1", "rsrp": -90, "rsrq": -10},
            {"timestamp": "2026-08-12T10:01:00Z", "serving_cell": "CELL2", "rsrp": -95, "rsrq": -12},
            {"timestamp": "2026-08-12T10:02:00Z", "serving_cell": "UNKNOWN", "rsrp": -100, "rsrq": -15},
            {"timestamp": "2026-08-12T10:02:00Z", "serving_cell": "256", "rsrp": -85, "rsrq": -8},  # same timestamp, different cell
            {"timestamp": "2026-08-12T10:03:00Z", "serving_cell": "SR-SPCNJ9_13", "rsrp": -98, "rsrq": -15},
        ]
        monkeypatch.setattr(database, "get_vip_series", lambda *a, **k: rows)
        return rows

    def test_vip_series_resolves_sites(self, api, mock_db_series):
        # Célula com ID exato (CELL1), substring (CELL2), não mapeada (UNKNOWN), decodificação ECI/NCI (256 -> Site 1)
        # Note: 256 // 256 = 1 (se o site tiver id "1" ou name "1", ele acha)
        event_cfg = {
            "id": "evt1",
            "name": "Test Event 1",
            "sites": [
                {"id": "SITE_A", "name": "Site Alpha", "lat": 0, "lng": 0, "cells": ["CELL1", {"id": "CELL_X"}]},
                {"id": "SITE_B", "name": "Site Beta", "lat": 0, "lng": 0, "cells": [{"id": "CELL2"}]},
                {"id": "1", "name": "Site One", "lat": 0, "lng": 0, "cells": []}
            ]
        }
        database.save_event(event_cfg)

        res = api.get_vip_series("evt1", "VIP_TEST", 60)
        assert res["ok"] is True
        series = res["series"]
        assert len(series) == 5

        # Covers AE2: Dois sites
        assert series[0]["serving_cell"] == "CELL1"
        assert series[0]["serving_site"] == "SITE_A"
        assert series[0]["serving_site_name"] == "Site Alpha"

        assert series[1]["serving_cell"] == "CELL2"
        assert series[1]["serving_site"] == "SITE_B"

        # Covers AE3: Desconhecido
        assert series[2]["serving_cell"] == "UNKNOWN"
        assert series[2]["serving_site"] is None
        assert series[2]["serving_site_name"] is None
        assert series[2]["rsrp"] == -100

        # Covers AE4: Mesmo timestamp, ordem mantida
        assert series[2]["timestamp"] == "2026-08-12T10:02:00Z"
        assert series[3]["timestamp"] == "2026-08-12T10:02:00Z"
        assert series[3]["serving_cell"] == "256"
        assert series[3]["serving_site"] == "1"

        # Site fora do evento: identifica o nome-base sem alterar in_event.
        assert series[4]["serving_site"] is None
        assert series[4]["serving_site_name"] == "SR-SPCNJ9"

    def test_vip_series_different_event_maps(self, api, mock_db_series):
        # Covers AE5: Eventos diferentes
        evt2 = {
            "id": "evt2",
            "name": "Test Event 2",
            "sites": [{"id": "SITE_X", "name": "Site X", "lat": 0, "lng": 0, "cells": ["CELL1"]}]
        }
        database.save_event(evt2)
        res = api.get_vip_series("evt2", "VIP_TEST")
        assert res["series"][0]["serving_site"] == "SITE_X"

    def test_vip_series_no_event(self, api, mock_db_series):
        # Covers AE6: Evento inexistente
        res = api.get_vip_series("nao_existe", "VIP_TEST")
        assert res["ok"] is True
        series = res["series"]
        assert all(r["serving_site"] is None for r in series)


def _cells(prefix: str, n: int) -> list:
    return [{"id": f"{prefix}-{i}", "azimuth": (i * 30) % 360} for i in range(n)]


def _twin_sites_event(sample_event, event_id="twin-merge"):
    return {
        **sample_event,
        "id": event_id,
        "integration": {
            "pm_tasks": [
                {"task_id": 2225, "tech": "4G"},
                {"task_id": 749, "tech": "NRCELL"},
                {"task_id": 748, "tech": "NRDUCELL"},
            ]
        },
        "sites": [
            {"id": "725483", "name": "SPSMG7", "lat": -23.640913, "lng": -46.710655,
             "is_event_site": True, "cells": _cells("4G-SPSMG7", 12)},
            {"id": "1774059", "name": "SPSMG7", "lat": -23.640913, "lng": -46.710655,
             "is_event_site": True, "cells": _cells("5G-SPSMG7", 3)},
            {"id": "725471", "name": "SPSMH1", "lat": -23.639723, "lng": -46.721558,
             "is_event_site": True, "cells": _cells("4G-SPSMH1", 6)},
            {"id": "1774047", "name": "SPSMH1", "lat": -23.639723, "lng": -46.721558,
             "is_event_site": True, "cells": _cells("5G-SPSMH1", 2)},
            {"id": "725469", "name": "SPSMH2", "lat": -23.639723, "lng": -46.721558,
             "is_event_site": True, "cells": _cells("4G-SPSMH2", 10)},
        ],
    }


def _insert_site_kpi(event_id, site_id, technology, value, ts="2026-08-19T12:00:00Z",
                     metric="utilization_dl"):
    database.insert_kpi_batch([{
        "event_id": event_id,
        "site_id": site_id,
        "cell_id": "__site__",
        "metric": metric,
        "value": value,
        "timestamp": ts,
        "scope": "SITE",
        "technology": technology,
    }])


def _insert_cell_kpi(event_id, site_id, cell_id, technology, value,
                     ts="2026-08-19T12:00:00Z", metric="utilization_dl"):
    database.insert_kpi_batch([{
        "event_id": event_id,
        "site_id": site_id,
        "cell_id": cell_id,
        "metric": metric,
        "value": value,
        "timestamp": ts,
        "scope": "CELL",
        "technology": technology,
    }])


class TestEventViewIndex:
    @staticmethod
    def _linear_site(merged, site_id):
        for site in merged:
            if site["id"] == site_id:
                return site
            if any(member.get("site_id") == site_id
                   for member in site.get("members") or []):
                return site
        return None

    @staticmethod
    def _linear_cell_owner(merged, cell_id):
        wanted = str(cell_id or "").upper()
        for site in merged:
            for cell in site.get("cells") or []:
                candidate = cell if isinstance(cell, str) else cell.get("id", "")
                if str(candidate).upper() == wanted:
                    return site
        return None

    @staticmethod
    def _linear_alarm_site(sites, source):
        if not source:
            return None, None
        src = str(source).strip().upper()
        if not src:
            return None, None
        for site in sites:
            for key in Api._site_identity_keys(site):
                key_upper = key.upper()
                if src == key_upper or src.startswith(key_upper) or key_upper in src:
                    return site.get("id"), site.get("name")
            site_name = (site.get("name") or "").upper()
            if site_name and (site_name in src or src in site_name):
                return site.get("id"), site.get("name")
        return None, None

    def test_view_resolve_site_por_id_fundido_e_por_membro(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "view-site-index")
        database.save_event(event)
        view = api._event_view(database.get_event(event["id"]))

        for site in view.sites:
            assert api._find_merged_site(view.sites, site["id"]) is site
            assert api._find_merged_site(view.sites, site["id"]) is self._linear_site(
                view.sites, site["id"])
            for member in site.get("members") or []:
                member_id = member["site_id"]
                assert api._find_merged_site(view.sites, member_id) is site
                assert api._find_merged_site(
                    view.sites, member_id) is self._linear_site(view.sites, member_id)
        assert api._find_merged_site(view.sites, "site-inexistente") is None

    def test_view_resolve_celula_para_o_site_dono(self, api, sample_event):
        event = _twin_sites_event(sample_event, "view-cell-index")
        database.save_event(event)
        view = api._event_view(database.get_event(event["id"]))

        for site in view.sites:
            for cell in site.get("cells") or []:
                cell_id = cell if isinstance(cell, str) else cell["id"]
                assert api._find_site_for_cell(
                    view.sites, cell_id) is self._linear_cell_owner(view.sites, cell_id)
        assert api._find_site_for_cell(view.sites, "celula-inexistente") is None

    def test_view_invalida_quando_o_cadastro_muda(self, api, sample_event):
        event = _twin_sites_event(sample_event, "view-invalidation")
        database.save_event(event)

        first_config = database.get_event(event["id"])
        first_view = api._event_view(first_config)
        assert api._event_view(database.get_event(event["id"])) is first_view

        changed = copy.deepcopy(event)
        changed["sites"].append({
            "id": "NEW-SITE", "name": "NEW-SITE", "lat": 0, "lng": 0,
            "cells": [{"id": "4G-NEW-SITE-A"}],
        })
        database.save_event(changed)
        changed_config = database.get_event(event["id"])
        changed_view = api._event_view(changed_config)

        assert changed_config["_config_digest"] != first_config["_config_digest"]
        assert changed_view is not first_view
        assert changed_view.by_site_id["NEW-SITE"]["id"] == "NEW-SITE"

    def test_caches_mantem_somente_a_versao_atual_e_a_anterior(
            self, api, sample_event):
        for version in range(3):
            event = copy.deepcopy(sample_event)
            event["name"] = f"Evento versão {version}"
            database.save_event(event)
            api._event_view(database.get_event(event["id"]))

        assert len(database._event_config_cache) == 2
        assert len(api._event_views) == 2

    def test_get_event_devolve_copia_rasa(
            self, api, sample_event, monkeypatch):
        database.save_event(sample_event)
        real_loads = database.json.loads
        parse_count = 0

        def counted_loads(*args, **kwargs):
            nonlocal parse_count
            parse_count += 1
            return real_loads(*args, **kwargs)

        monkeypatch.setattr(database.json, "loads", counted_loads)
        first = database.get_event(sample_event["id"])
        first["status"] = "LOCAL-ONLY"
        second = database.get_event(sample_event["id"])

        assert first is not second
        assert first["sites"] is second["sites"]
        assert second["status"] == sample_event["status"]
        assert first["_config_digest"] == second["_config_digest"]
        assert parse_count == 1

        database.save_event(second)
        raw = database.get_conn().execute(
            "SELECT config_json FROM events WHERE id = ?", (sample_event["id"],)
        ).fetchone()["config_json"]
        assert "_config_digest" not in raw

    def test_sanitize_event_remove_config_digest(self, api, sample_event):
        event = {**sample_event, "_config_digest": "internal"}

        sanitized = api._sanitize_event(event)

        assert "_config_digest" not in sanitized

    def test_resolve_site_for_source_mantem_paridade(self, api, sample_event):
        event = _twin_sites_event(sample_event, "view-alarm-parity")
        event["sites"].append({
            "id": "FRIENDLY-ID", "name": "Friendly Name", "lat": 0, "lng": 0,
            "cells": [{"id": "4G-FRIENDLY-A"}],
        })
        database.save_event(event)
        sites = api._event_view(database.get_event(event["id"])).sites
        sources = (
            "SPSMG7",
            "SPSMG7-SECTOR",
            "OSS-SPSMG7-ALARM",
            "Friendly Name",
            "1774059",
            "UNKNOWN-SITE",
        )

        for source in sources:
            assert api._resolve_site_for_source(
                sites, source) == self._linear_alarm_site(sites, source)

    def test_get_sites_em_evento_grande_fica_abaixo_do_orcamento(
            self, api, sample_event, monkeypatch):
        site_count = 1500
        sites = [
            {
                "id": f"SITE-{index:04d}",
                "name": f"SITE{index:04d}",
                "lat": -23.0 + index / 100000,
                "lng": -46.0 + index / 100000,
                "cells": [{
                    "id": f"4G-SITE{index:04d}-A",
                    "earfcn": index % 50,
                }],
            }
            for index in range(site_count)
        ]
        clusters = [
            {
                "id": f"cluster-{cluster_index}",
                "name": f"Cluster {cluster_index}",
                "site_ids": [
                    f"SITE-{(cluster_index * 29 + offset * 7) % site_count:04d}"
                    for offset in range(140)
                ],
            }
            for cluster_index in range(50)
        ]
        event = {
            **sample_event,
            "id": "large-event-view-budget",
            "integration": {"pm_tasks": [{"task_id": 1, "tech": "4G"}]},
            "sites": sites,
            "clusters": clusters,
        }
        database.save_event(event)
        monkeypatch.setattr(database, "get_latest_kpi", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            database, "get_latest_kpi_by_metric", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            database, "get_latest_site_kpi_by_metric", lambda *_args, **_kwargs: [])

        started = time.perf_counter()
        result = api.get_sites(event["id"])
        elapsed = time.perf_counter() - started

        assert len(result) == site_count
        assert elapsed < 5.0, f"get_sites levou {elapsed:.2f}s"


class TestSiteMerge:
    def test_sites_com_mesmo_nome_e_coordenada_sao_fundidos(self, api, sample_event):
        event = _twin_sites_event(sample_event)
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        spsmg7 = sites["SPSMG7"]

        assert "725483" not in sites
        assert "1774059" not in sites
        assert spsmg7["tech_families"] == ["4G", "5G"]
        assert len(spsmg7["cells"]) == 15
        assert {m["site_id"]: m["family"] for m in spsmg7["members"]} == {
            "725483": "4G", "1774059": "5G",
        }

    def test_sites_com_prefixo_de_tecnologia_diferente_sao_fundidos(
            self, api, sample_event):
        # Salvador: a EP nomeia o gêmeo 4G "SR-SACAL5" e o 5G "5G-SACAL5" — mesmo
        # site físico, coordenada igual, só o prefixo de tecnologia difere.
        event = {
            **sample_event,
            "id": "prefix-merge",
            "sites": [
                {"id": "462982", "name": "SR-SACAL5", "lat": -12.975111, "lng": -38.440582,
                 "is_event_site": True, "cells": _cells("4G-SACAL5", 4)},
                {"id": "1511558", "name": "5G-SACAL5", "lat": -12.975111, "lng": -38.440582,
                 "is_event_site": True, "cells": _cells("5G-SACAL5", 3)},
            ],
        }
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        merged = sites["SACAL5"]

        assert "462982" not in sites
        assert "1511558" not in sites
        assert merged["tech_families"] == ["4G", "5G"]
        assert {m["site_id"]: m["family"] for m in merged["members"]} == {
            "462982": "4G", "1511558": "5G",
        }

    def test_site_sem_gemeo_permanece_intacto(self, api, sample_event):
        event = _twin_sites_event(sample_event)
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}

        assert "725469" in sites
        assert sites["725469"]["name"] == "SPSMH2"
        assert sites["725469"]["tech_families"] == ["4G"]
        assert len(sites["725469"]["cells"]) == 10
        assert sites["725469"]["members"] == [{"site_id": "725469", "family": "4G"}]

    def test_nome_igual_em_coordenada_distante_funde_sem_validacao(
            self, api, sample_event, caplog):
        event = {
            **sample_event,
            "id": "homonym-distant",
            "sites": [
                {"id": "A1", "name": "SPSMG7", "lat": -23.640913, "lng": -46.710655,
                 "cells": _cells("4G-SPSMG7", 2)},
                {"id": "B1", "name": "SPSMG7", "lat": -23.650913, "lng": -46.710655,
                 "cells": _cells("5G-SPSMG7", 2)},
            ],
        }
        database.save_event(event)

        with caplog.at_level("WARNING"):
            sites = api.get_sites(event["id"])
        assert [site["id"] for site in sites] == ["SPSMG7"]
        assert {member["site_id"] for member in sites[0]["members"]} == {"A1", "B1"}
        assert len(sites[0]["cells"]) == 4
        assert "não fundidos" not in caplog.text

    @pytest.mark.parametrize("inside_a,inside_b,expected_conflicts", [
        (True, True, 1),
        (False, False, 0),
        (True, False, 0),
        (False, True, 0),
    ])
    def test_validacao_inativa_de_homonimos_distantes_fica_preservada(
            self, api, sample_event, caplog, inside_a, inside_b, expected_conflicts):
        event = {
            **sample_event,
            "id": f"homonym-outside-{inside_a}-{inside_b}",
            "sites": [
                {"id": "A1", "name": "SPSMG7", "lat": -23.640913,
                 "lng": -46.710655, "is_event_site": inside_a,
                 "cells": _cells("4G-SPSMG7", 2)},
                {"id": "B1", "name": "SPSMG7", "lat": -23.650913,
                 "lng": -46.710655, "is_event_site": inside_b,
                 "cells": _cells("5G-SPSMG7", 2)},
            ],
        }
        with caplog.at_level("WARNING"):
            conflicts = api._validate_distant_homonyms(event)

        assert len(conflicts) == expected_conflicts
        assert ("não fundidos" in caplog.text) is bool(expected_conflicts)

    def test_valor_da_lista_nao_depende_da_ordem_das_linhas_site(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "twin-order")
        database.save_event(event)

        _insert_site_kpi(event["id"], "725483", "4G", 10.0)
        _insert_site_kpi(event["id"], "1774059", "5G_NRDUCELL", 80.0)
        first = {site["id"]: site["metric_value"] for site in api.get_sites(
            event["id"], metric="utilization_dl")}

        event_rev = _twin_sites_event(sample_event, "twin-order-rev")
        database.save_event(event_rev)
        _insert_site_kpi(event_rev["id"], "1774059", "5G_NRDUCELL", 80.0)
        _insert_site_kpi(event_rev["id"], "725483", "4G", 10.0)
        second = {site["id"]: site["metric_value"] for site in api.get_sites(
            event_rev["id"], metric="utilization_dl")}

        assert first["SPSMG7"] == second["SPSMG7"] == 80.0

    def test_serie_do_site_fundido_traz_uma_entrada_por_tecnologia(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "twin-series")
        database.save_event(event)
        _insert_site_kpi(event["id"], "725483", "4G", 10.0, "2026-08-19T12:00:00Z")
        _insert_site_kpi(event["id"], "725483", "4G", 12.0, "2026-08-19T12:05:00Z")
        _insert_site_kpi(event["id"], "1774059", "5G_NRDUCELL", 40.0, "2026-08-19T12:00:00Z")
        _insert_site_kpi(event["id"], "1774059", "5G_NRDUCELL", 42.0, "2026-08-19T12:05:00Z")

        result = api.get_kpi_series(
            event["id"], "SPSMG7", "utilization_dl", minutes=0)

        by_tech = {item["technology"]: item for item in result["series"]}
        assert set(by_tech) == {"4G", "5G"}
        assert result["values"] == []
        assert 10.0 in by_tech["4G"]["values"]
        assert 12.0 in by_tech["4G"]["values"]
        assert 40.0 in by_tech["5G"]["values"]
        assert 42.0 in by_tech["5G"]["values"]
        assert 40.0 not in by_tech["4G"]["values"]
        assert 10.0 not in by_tech["5G"]["values"]

    def test_media_do_site_fundido_traz_uma_media_por_tecnologia(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "twin-media")
        database.save_event(event)
        ts = "2026-08-19T12:00:00Z"
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 10.0, ts)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-1", "4G", 20.0, ts)
        _insert_cell_kpi(event["id"], "1774059", "5G-SPSMG7-0", "5G_NRDUCELL", 40.0, ts)
        _insert_cell_kpi(event["id"], "1774059", "5G-SPSMG7-1", "5G_NRDUCELL", 50.0, ts)
        _insert_site_kpi(event["id"], "725483", "4G", 99.0, ts)
        _insert_site_kpi(event["id"], "1774059", "5G_NRDUCELL", 99.0, ts)

        result = api.get_kpi_series(
            event["id"], "SPSMG7", "utilization_dl", minutes=0, cell_id="__media__")
        by_tech = {item["technology"]: item for item in result["series"]}

        assert set(by_tech) == {"4G", "5G"}
        assert result["values"] == []
        assert by_tech["4G"]["values"] == [15.0]
        assert by_tech["5G"]["values"] == [45.0]
        assert 99.0 not in by_tech["4G"]["values"]
        assert 99.0 not in by_tech["5G"]["values"]

    def test_site_completo_do_fundido_traz_as_celulas_por_familia(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "twin-site-cells")
        database.save_event(event)
        ts = "2026-08-19T12:00:00Z"
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 10.0, ts)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-1", "4G", 20.0, ts)
        _insert_cell_kpi(event["id"], "1774059", "5G-SPSMG7-0", "5G_NRDUCELL", 40.0, ts)
        _insert_site_kpi(event["id"], "725483", "4G", 10.0, ts)
        _insert_site_kpi(event["id"], "1774059", "5G_NRDUCELL", 40.0, ts)

        all_cells = api.get_kpi_series(
            event["id"], "SPSMG7", "utilization_dl", minutes=0, cell_id="__all__")
        only_5g = api.get_kpi_series(
            event["id"], "SPSMG7", "utilization_dl", minutes=0,
            cell_id="__all__", technology_family="5G")
        only_4g = api.get_kpi_series(
            event["id"], "SPSMG7", "utilization_dl", minutes=0,
            cell_id="__all__", technology_family="4G")

        assert set(all_cells["cells_data"]) == {
            "4G-SPSMG7-0", "4G-SPSMG7-1", "5G-SPSMG7-0",
        }
        assert set(only_5g["cells_data"]) == {"5G-SPSMG7-0"}
        assert set(only_4g["cells_data"]) == {"4G-SPSMG7-0", "4G-SPSMG7-1"}

    def test_alarme_e_vip_apontam_para_o_id_fundido(self, api, sample_event):
        event = _twin_sites_event(sample_event, "twin-alarm-vip")
        database.save_event(event)
        eid = event["id"]

        database.insert_alarms_batch([
            {"csn": 11, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": "Cell Unavailable", "severity": "Major",
             "source": "1774059", "ip": "", "location": "",
             "occur_time": "2026-08-19T12:00:00Z",
             "arrive_time": "2026-08-19T12:00:00Z", "additional_info": "",
             "collected_at": "2026-08-19T12:00:00Z"},
        ])
        alarms = {row["csn"]: row for row in api.get_alarms(eid)}
        assert alarms[11]["serving_site"] == "SPSMG7"
        assert alarms[11]["in_event"] is True

        vip = database.save_vip({"name": "VIP 5G Twin", "oss": "SP", "cliente": "TIM"})
        api.assign_vip_to_event(eid, vip["id"], task_id=1)
        database.insert_vip_batch([{
            "event_id": eid,
            "vip_name": "VIP 5G Twin",
            "task_id": 1,
            "serial_no": 1,
            "timestamp": "2026-08-19T12:00:00Z",
            "serving_cell": "5G-SPSMG7-0",
            "rsrp": -80.0,
            "rsrq": -8.0,
            "in_event": True,
        }])
        vips = {row["name"]: row for row in api.get_vips(eid)}
        assert vips["VIP 5G Twin"]["serving_site"] == "SPSMG7"

    def test_filtro_de_tecnologia_recorta_as_celulas(self, api, sample_event):
        event = _twin_sites_event(sample_event, "twin-cells")
        database.save_event(event)

        cells_5g = api.get_site_cells(event["id"], "SPSMG7", "5G")
        cells_4g = api.get_site_cells(event["id"], "SPSMG7", "4G")
        cells_all = api.get_site_cells(event["id"], "SPSMG7")

        assert len(cells_5g) == 3
        assert len(cells_4g) == 12
        assert len(cells_all) == 15
        assert {cell["family"] for cell in cells_5g} == {"5G"}
        assert {cell["family"] for cell in cells_4g} == {"4G"}


def _event_with_clusters(sample_event, clusters, event_id="cluster-event", extra_sites=None):
    sites = list(sample_event["sites"])
    if extra_sites:
        sites.extend(extra_sites)
    return {**sample_event, "id": event_id, "sites": sites, "clusters": clusters}


class TestClusters:
    def test_cluster_aceita_o_mesmo_site_em_mais_de_um_grupo(self, api, sample_event):
        extra_site = {
            "id": "SR-EXTRA", "name": "SR-EXTRA", "lat": -23.59, "lng": -46.68,
            "is_event_site": True, "cells": _cells("SR-EXTRA", 2),
        }
        clusters = [
            {"id": "sul", "name": "Sul", "color": "#F85149",
             "site_ids": ["SR-SPPNB2", "SR-EXTRA"]},
            {"id": "oeste", "name": "Oeste", "color": "#388BFD",
             "site_ids": ["SR-SPPNB2"]},
        ]
        event = _event_with_clusters(sample_event, clusters, extra_sites=[extra_site])
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        assert set(sites["SR-SPPNB2"]["cluster_ids"]) == {"sul", "oeste"}
        assert sites["SR-EXTRA"]["cluster_ids"] == ["sul"]

    def test_poligono_nao_altera_pertencimento_depois_de_salvo(self, api, sample_event):
        clusters = [{
            "id": "sul", "name": "Sul", "color": "#F85149",
            "site_ids": ["SR-SPPNB2"],
            # Polígono deliberadamente longe do site — pertencimento é só site_ids (§1).
            "polygon": [[10.0, 10.0], [10.0, 10.1], [10.1, 10.1], [10.1, 10.0]],
        }]
        event = _event_with_clusters(sample_event, clusters, event_id="cluster-polygon")
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        assert sites["SR-SPPNB2"]["cluster_ids"] == ["sul"]
        assert api.get_clusters(event["id"])[0]["site_count"] == 1

    def test_cluster_com_site_fundido_cobre_as_duas_tecnologias(self, api, sample_event):
        event = _twin_sites_event(sample_event, "cluster-merged")
        event["clusters"] = [{
            "id": "campo", "name": "Campo", "color": "#388BFD",
            # id cru de um dos gêmeos — precisa resolver para o site fundido inteiro.
            "site_ids": ["725483"],
        }]
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        assert sites["SPSMG7"]["cluster_ids"] == ["campo"]
        assert api.get_clusters(event["id"])[0]["site_count"] == 1

        _insert_site_kpi(event["id"], "725483", "4G", 10.0)
        _insert_site_kpi(event["id"], "1774059", "5G_NRDUCELL", 40.0)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="cluster", scope_id="campo")
        by_tech = {item["technology"]: item for item in result["series"]}
        assert set(by_tech) == {"4G", "5G"}
        assert 10.0 in by_tech["4G"]["values"]
        assert 40.0 in by_tech["5G"]["values"]

    def test_serie_de_cluster_combina_linhas_site_dos_membros(self, api, sample_event):
        extra_site = {
            "id": "SR-EXTRA", "name": "SR-EXTRA", "lat": -23.59, "lng": -46.68,
            "is_event_site": True, "cells": _cells("SR-EXTRA", 2),
        }
        clusters = [{"id": "sul", "name": "Sul", "color": "#F85149",
                     "site_ids": ["SR-SPPNB2", "SR-EXTRA"]}]
        event = _event_with_clusters(
            sample_event, clusters, event_id="cluster-series", extra_sites=[extra_site])
        database.save_event(event)

        ts = "2026-08-19T12:00:00Z"
        _insert_site_kpi(event["id"], "SR-SPPNB2", "4G", 10.0, ts)
        _insert_site_kpi(event["id"], "SR-EXTRA", "4G", 20.0, ts)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="cluster", scope_id="sul")

        assert len(result["series"]) == 1
        # utilization_dl combina por máximo (pior caso) entre os sites do cluster.
        assert result["series"][0]["values"] == [20.0]

    def test_cluster_parcial_agrega_somente_as_celulas_selecionadas(self, api, sample_event):
        event = _twin_sites_event(sample_event, "cluster-partial-cells")
        event["clusters"] = [{
            "id": "setor-a", "name": "Setor A", "color": "#F85149",
            "members": [{
                "site_id": "725483",
                "cell_ids": ["4G-SPSMG7-0", "4G-SPSMG7-1"],
            }],
        }]
        database.save_event(event)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 10.0)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-1", "4G", 80.0)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-2", "4G", 99.0)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="cluster", scope_id="setor-a")

        assert result["series"][0]["technology"] == "4G"
        assert result["series"][0]["values"] == [80.0]
        cluster = api.get_clusters(event["id"])[0]
        assert cluster["site_count"] == 1
        assert cluster["cell_count"] == 2
        assert cluster["has_partial_selection"] is True

    def test_get_clusters_retorna_contagem_cor_e_poligono(self, api, sample_event):
        clusters = [{"id": "sul", "name": "Sul", "color": "#F85149",
                     "site_ids": ["SR-SPPNB2"], "polygon": [[1.0, 2.0]]}]
        event = _event_with_clusters(sample_event, clusters, event_id="cluster-get")
        database.save_event(event)

        assert api.get_clusters(event["id"]) == [{
            "id": "sul", "name": "Sul", "color": "#F85149",
            "site_count": 1, "cell_count": 3,
            "has_partial_selection": False, "polygon": [[1.0, 2.0]],
        }]


    def test_cluster_de_uma_tecnologia_so_declara_a_familia(self, api, sample_event):
        # A visão geral usa `family` para esconder o cluster na aba da outra
        # tecnologia. Sem ele o cluster de células 4G sobrevive à troca para 5G
        # como escopo invisível: vira chip, entra na consulta e renderiza painel
        # vazio, sem linha no seletor para desmarcá-lo.
        event = _twin_sites_event(sample_event, "cluster-family")
        event["clusters"] = [
            {"id": "so-4g", "name": "Só 4G", "site_ids": ["725483"],
             "members": [{"site_id": "725483", "all_cells": True}]},
            {"id": "fundido", "name": "Fundido", "site_ids": ["SPSMG7"]},
        ]
        database.save_event(event)

        clusters = {c["id"]: c for c in api.get_clusters(event["id"])}
        assert clusters["so-4g"]["family"] == "4G"
        # Cluster de site inteiro cobre as duas tecnologias: sem família, ele
        # atravessa a troca de aba.
        assert "family" not in clusters["fundido"]


class TestEarfcnClusters:
    def test_sem_dlearfcn_nao_inventa_cluster(self, api, sample_event):
        database.save_event(sample_event)

        assert all(c.get("source") != "earfcn" for c in api.get_clusters(sample_event["id"]))

    def test_familia_configurada_e_resolvida_uma_vez_por_cadastro(
            self, api, sample_event, monkeypatch):
        event = copy.deepcopy(sample_event)
        event["sites"][0]["cells"] = [
            {"id": f"CELL-{index}", "earfcn": index % 5}
            for index in range(100)
        ]
        original = Api._single_configured_family.__func__
        calls = 0

        def counted(cls, config):
            nonlocal calls
            calls += 1
            return original(cls, config)

        monkeypatch.setattr(Api, "_single_configured_family", classmethod(counted))

        api._earfcn_clusters(event)

        assert calls == 1

    def test_agrupa_celulas_4g_pela_portadora(self, api, sample_event):
        event = {
            **sample_event,
            "id": "earfcn-event",
            "sites": [{
                **sample_event["sites"][0],
                "cells": [
                    {"id": "SR-SPPNB2_1", "azimuth": 0, "beamwidth": 120,
                     "tech": "4G", "earfcn": "1276"},
                    {"id": "SR-SPPNB2_2", "azimuth": 120, "beamwidth": 120,
                     "tech": "4G", "earfcn": "1276"},
                    {"id": "SR-SPPNB2_3", "azimuth": 240, "beamwidth": 120,
                     "tech": "4G", "earfcn": "1700"},
                ],
            }],
        }
        database.save_event(event)

        clusters = {c["id"]: c for c in api.get_clusters(event["id"])}

        assert clusters["earfcn-1276"]["name"] == "Portadora 1276"
        assert clusters["earfcn-1276"]["source"] == "earfcn"
        assert clusters["earfcn-1276"]["cell_count"] == 2
        assert clusters["earfcn-1276"]["has_partial_selection"] is True
        assert clusters["earfcn-1700"]["cell_count"] == 1
        assert clusters["earfcn-1700"]["has_partial_selection"] is True

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        assert "earfcn-1276" in sites["SR-SPPNB2"]["cluster_ids"]
        assert "earfcn-1700" in sites["SR-SPPNB2"]["cluster_ids"]

    def test_5g_gera_portadora_propria_sem_misturar_com_4g(self, api, sample_event):
        event = _twin_sites_event(sample_event, "earfcn-twin")
        for site in event["sites"]:
            for cell in site["cells"]:
                if str(cell["id"]).startswith("4G-"):
                    cell["earfcn"] = "1276"
                    cell["tech"] = "4G"
                else:
                    cell["earfcn"] = "627264"
                    cell["tech"] = "5G"
        database.save_event(event)

        clusters = {c["id"]: c for c in api.get_clusters(event["id"])}

        assert "earfcn-1276" in clusters
        assert "earfcn-627264" in clusters
        # 12 + 6 + 10 células 4G dos três sites do fixture de gêmeos.
        assert clusters["earfcn-1276"]["cell_count"] == 28
        assert clusters["earfcn-1276"]["family"] == "4G"
        # 3 + 2 células 5G dos dois sites gêmeos.
        assert clusters["earfcn-627264"]["cell_count"] == 5
        assert clusters["earfcn-627264"]["family"] == "5G"

    def test_serie_do_cluster_de_portadora_agrega_so_as_celulas_da_earfcn(
            self, api, sample_event):
        event = {
            **sample_event,
            "id": "earfcn-series",
            "sites": [{
                **sample_event["sites"][0],
                "cells": [
                    {"id": "SR-SPPNB2_1", "azimuth": 0, "tech": "4G", "earfcn": "1276"},
                    {"id": "SR-SPPNB2_2", "azimuth": 120, "tech": "4G", "earfcn": "1700"},
                ],
            }],
        }
        database.save_event(event)
        _insert_cell_kpi(event["id"], "SR-SPPNB2", "SR-SPPNB2_1", "4G", 10.0)
        _insert_cell_kpi(event["id"], "SR-SPPNB2", "SR-SPPNB2_2", "4G", 90.0)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="cluster", scope_id="earfcn-1276")

        assert result["series"][0]["values"] == [10.0]

    def test_cluster_salvo_com_mesmo_id_nao_e_duplicado(self, api, sample_event):
        event = {
            **sample_event,
            "id": "earfcn-override",
            "clusters": [{
                "id": "earfcn-1276", "name": "Banda 1800", "color": "#ffffff",
                "members": [{"site_id": "SR-SPPNB2", "all_cells": True}],
            }],
            "sites": [{
                **sample_event["sites"][0],
                "cells": [
                    {"id": "SR-SPPNB2_1", "azimuth": 0, "tech": "4G", "earfcn": "1276"},
                ],
            }],
        }
        database.save_event(event)

        earfcn = [c for c in api.get_clusters(event["id"]) if c["id"] == "earfcn-1276"]

        assert len(earfcn) == 1
        assert earfcn[0]["name"] == "Banda 1800"


class TestSiteCarrierScope:
    """`scope="site_carrier"`: recorte de um site por portadora (EARFCN/NR-ARFCN).

    `scope_id` é `<site_id>::<earfcn>`; reaproveita `_cluster_series_by_family`
    sem nenhuma agregação nova.
    """

    def _event_com_portadoras(self, sample_event, event_id):
        event = _twin_sites_event(sample_event, event_id)
        for site in event["sites"]:
            for cell in site["cells"]:
                if str(cell["id"]).startswith("4G-"):
                    cell["earfcn"] = "1276"
                    cell["tech"] = "4G"
                else:
                    cell["earfcn"] = "627264"
                    cell["tech"] = "5G"
        return event

    def test_resolve_a_agregacao_das_celulas_daquela_portadora(self, api, sample_event):
        event = self._event_com_portadoras(sample_event, "site-carrier-scope")
        database.save_event(event)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 10.0)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-1", "4G", 80.0)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="site_carrier", scope_id="SPSMG7::1276", technology_family="4G")

        assert result["ok"] is True
        assert result["series"][0]["technology"] == "4G"
        assert result["series"][0]["values"] == [80.0]

    def test_site_gemeo_4g_5g_so_puxa_o_membro_da_portadora(self, api, sample_event):
        event = self._event_com_portadoras(sample_event, "site-carrier-twin")
        database.save_event(event)
        _insert_cell_kpi(event["id"], "1774059", "5G-SPSMG7-0", "5G", 40.0)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 99.0)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="site_carrier", scope_id="SPSMG7::627264", technology_family="5G")

        assert result["series"][0]["technology"] == "5G"
        assert result["series"][0]["values"] == [40.0]

    def test_earfcn_inexistente_devolve_serie_vazia_sem_erro(self, api, sample_event):
        event = self._event_com_portadoras(sample_event, "site-carrier-missing")
        database.save_event(event)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="site_carrier", scope_id="SPSMG7::999999", technology_family="4G")

        assert result["ok"] is True
        assert result["series"] == []

    def test_get_kpi_overview_multi_aceita_o_escopo(self, api, sample_event):
        event = self._event_com_portadoras(sample_event, "site-carrier-multi")
        database.save_event(event)
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 55.0)

        result = api.get_kpi_overview_multi(
            event["id"],
            [{"scope": "site_carrier", "scope_id": "SPSMG7::1276"}],
            "4G",
            minutes=0,
        )

        assert result["ok"] is True
        assert result["series"][0]["scope"] == "site_carrier"
        assert result["series"][0]["scope_id"] == "SPSMG7::1276"
        assert result["series"][0]["metrics"]["utilization_dl"] == [55.0]

    def test_get_sites_lista_as_portadoras_do_site(self, api, sample_event):
        event = self._event_com_portadoras(sample_event, "site-carrier-list")
        database.save_event(event)

        sites = {site["id"]: site for site in api.get_sites(event["id"])}
        carriers = {c["earfcn"]: c for c in sites["SPSMG7"]["carriers"]}

        assert carriers["1276"]["family"] == "4G"
        assert carriers["1276"]["cell_count"] == 12
        assert carriers["627264"]["family"] == "5G"
        assert carriers["627264"]["cell_count"] == 3


class TestDisplaySiteName:
    """Nome exibido a partir do `nename` da EP (convenção de nomes da TIM).

    Sem hífen o nome já é o do site; com hífen, o que vem antes é o indicador de
    tecnologia e o nome do site é o último elemento. O bruto continua acessível
    em `original_name`.
    """

    def _event(self, sample_event, event_id, site_name):
        return {
            **sample_event,
            "id": event_id,
            "sites": [{
                "id": "462982", "name": site_name,
                "lat": -12.975111, "lng": -38.440582,
                "is_event_site": True,
                "cells": [{"id": f"{site_name}_1", "azimuth": 0}],
            }],
        }

    def test_nome_sem_hifen_fica_intacto(self, api, sample_event):
        database.save_event(self._event(sample_event, "name-plain", "SPSMG7"))

        site = api.get_sites("name-plain")[0]

        assert site["name"] == "SPSMG7"
        assert site["original_name"] == "SPSMG7"

    def test_nome_com_hifen_exibe_o_trecho_depois_do_indicador(self, api, sample_event):
        database.save_event(self._event(sample_event, "name-prefixed", "5D-SACEO1"))

        site = api.get_sites("name-prefixed")[0]

        assert site["name"] == "SACEO1"
        assert site["original_name"] == "5D-SACEO1"

    def test_varios_hifens_pegam_o_ultimo_elemento(self, api, sample_event):
        database.save_event(self._event(sample_event, "name-multi", "SR-BA-SACEO1"))

        assert api.get_sites("name-multi")[0]["name"] == "SACEO1"

    def test_nome_terminado_em_hifen_mantem_o_bruto(self, api, sample_event):
        # Sem nada depois do hífen não há nome a extrair — melhor o bruto que vazio.
        database.save_event(self._event(sample_event, "name-trailing", "5G-"))

        assert api.get_sites("name-trailing")[0]["name"] == "5G-"

    def test_gemeo_fundido_exibe_o_nome_tratado(self, api, sample_event):
        event = {
            **sample_event,
            "id": "name-twin",
            "sites": [
                {"id": "462982", "name": "SR-SACAL5", "lat": -12.975111, "lng": -38.440582,
                 "is_event_site": True, "cells": _cells("4G-SACAL5", 4)},
                {"id": "1511558", "name": "5G-SACAL5", "lat": -12.975111, "lng": -38.440582,
                 "is_event_site": True, "cells": _cells("5G-SACAL5", 3)},
            ],
        }
        database.save_event(event)

        site = api.get_sites("name-twin")[0]

        assert site["name"] == "SACAL5"
        assert site["original_name"] == "SR-SACAL5"


class TestAlertDisplayNames:
    def _insert(self, event_id, **fields):
        payload = {
            "event_id": event_id,
            "level": "EVENT",
            "severity": "CRITICAL",
            "timestamp": "2026-06-01T10:00:00Z",
            **fields,
        }
        database.insert_alert(payload)

    def test_utilizacao_mostra_nome_do_site(self, api, sample_event):
        database.save_event(sample_event)
        self._insert(
            sample_event["id"],
            site_id="SR-SPPNB2", cell_id="SR-SPPNB2_1",
            message="Utilização DL crítica: 96% em SR-SPPNB2",
        )

        alert = api.get_alerts(sample_event["id"])[0]

        # `SR-` é o indicador de tecnologia: o nome exibido é o que vem depois do
        # hífen (ver TestDisplaySiteName). O id do site fundido não muda.
        assert alert["display_name"] == "SPPNB2"
        assert alert["serving_site"] == "SR-SPPNB2"

    def test_acessibilidade_mostra_nome_da_celula(self, api, sample_event):
        database.save_event(sample_event)
        self._insert(
            sample_event["id"],
            site_id="SR-SPPNB2", cell_id="SR-SPPNB2_1",
            message="Acessibilidade crítica: 50.0% na célula SR-SPPNB2_1",
        )

        assert api.get_alerts(sample_event["id"])[0]["display_name"] == "SR-SPPNB2_1"

    def test_enodebid_numerico_vira_nome_do_site_fundido(self, api, sample_event):
        event = _twin_sites_event(sample_event, "alert-merged")
        database.save_event(event)
        self._insert(
            event["id"],
            site_id="725483", cell_id="4G-SPSMG7-0",
            message="Utilização DL crítica: 96% em 725483",
        )

        alert = api.get_alerts(event["id"])[0]

        assert alert["display_name"] == "SPSMG7"
        assert alert["serving_site"] == "SPSMG7"
        assert "725483" not in alert["message"]
        assert "SPSMG7" in alert["message"]

    def test_rsrp_do_vip_mostra_a_celula(self, api, sample_event):
        database.save_event(sample_event)
        self._insert(
            sample_event["id"],
            site_id="SR-SPPNB2_1", cell_id="SR-SPPNB2_1",
            message="RSRP crítico para VIP: -112 dBm",
        )

        alert = api.get_alerts(sample_event["id"])[0]

        assert alert["display_name"] == "SR-SPPNB2_1"
        assert alert["serving_site"] == "SR-SPPNB2"


class TestEventCells:
    """`get_event_cells` alimenta o filtro por células da Visão Geral."""

    def test_lista_todas_as_celulas_com_o_site_dono(self, api, sample_event):
        database.save_event(sample_event)

        cells = api.get_event_cells(sample_event["id"])

        assert {c["id"] for c in cells} == {"SR-SPPNB2_1", "SR-SPPNB2_2", "SR-SPPNB2_3"}
        # site_id continua o id bruto; site_name é o nome tratado (sem `SR-`).
        assert all(c["site_id"] == "SR-SPPNB2" and c["site_name"] == "SPPNB2"
                   for c in cells)

    def test_recorta_por_familia_e_cobre_sites_fundidos(self, api, sample_event):
        event = _twin_sites_event(sample_event, "event-cells-twin")
        database.save_event(event)

        cells_5g = api.get_event_cells(event["id"], "5G")

        # SPSMG7 (3 células 5G) + SPSMH1 (2 células 5G), sites já fundidos por
        # nome+coordenada — a lista precisa cobrir o evento inteiro, não só um site.
        assert len(cells_5g) == 5
        assert {c["site_id"] for c in cells_5g} == {"SPSMG7", "SPSMH1"}
        assert {c["family"] for c in cells_5g} == {"5G"}


class TestCellScope:
    """`scope="cell"` na Visão Geral: terceiro escopo, ao lado de site e cluster."""

    def test_serie_de_celula_acha_o_site_dono_no_evento_inteiro(self, api, sample_event):
        event = _twin_sites_event(sample_event, "cell-scope-owner")
        database.save_event(event)
        # `scope_id` só traz o id da célula — o dono (site cru "725483", fundido
        # em "SPSMG7") precisa ser resolvido varrendo o evento inteiro.
        _insert_cell_kpi(event["id"], "725483", "4G-SPSMG7-0", "4G", 42.0)

        result = api.get_kpi_series(
            event["id"], None, "utilization_dl", minutes=0,
            scope="cell", scope_id="4G-SPSMG7-0")

        assert result["ok"] is True
        assert result["series"][0]["technology"] == "4G"
        assert result["series"][0]["values"] == [42.0]

    def test_celula_inexistente_devolve_serie_vazia_sem_erro(self, api, sample_event):
        database.save_event(sample_event)

        result = api.get_kpi_series(
            sample_event["id"], None, "utilization_dl", minutes=0,
            scope="cell", scope_id="NAO-EXISTE")

        assert result["ok"] is True
        assert result["series"] == []

    def test_get_kpi_overview_multi_aceita_escopo_de_celula(self, api, sample_event):
        database.save_event(sample_event)
        _insert_cell_kpi(sample_event["id"], "SR-SPPNB2", "SR-SPPNB2_1", "4G", 55.0)

        result = api.get_kpi_overview_multi(
            sample_event["id"],
            [{"scope": "cell", "scope_id": "SR-SPPNB2_1"}],
            "4G",
            minutes=0,
        )

        assert result["ok"] is True
        assert result["series"][0]["scope"] == "cell"
        assert result["series"][0]["scope_id"] == "SR-SPPNB2_1"
        assert result["series"][0]["metrics"]["utilization_dl"] == [55.0]


class TestMetricThresholds:
    """B2 — threshold com unidade. ``_metric_thresholds`` precisa aceitar o
    formato legado (número cru, usado por ``sample_event``) e o novo
    ({"value", "unit"}), e sempre devolver o objeto normalizado."""

    def test_accepts_legacy_raw_number(self, api, sample_event):
        event = {**sample_event, "id": "threshold-legacy",
                 "thresholds": {"utilization_warning": 80, "utilization_critical": 95}}
        database.save_event(event)

        result = api._metric_thresholds(event["id"], "utilization_dl")

        assert result == {"warning": {"value": 80, "unit": "%"},
                           "critical": {"value": 95, "unit": "%"}}

    def test_accepts_new_object_format(self, api, sample_event):
        event = {**sample_event, "id": "threshold-object",
                 "thresholds": {
                     "utilization_warning": {"value": 80, "unit": "%"},
                     "utilization_critical": {"value": 95, "unit": "%"},
                 }}
        database.save_event(event)

        result = api._metric_thresholds(event["id"], "utilization_dl")

        assert result == {"warning": {"value": 80, "unit": "%"},
                           "critical": {"value": 95, "unit": "%"}}

    def test_missing_threshold_is_none_not_zero(self, api, sample_event):
        event = {**sample_event, "id": "threshold-missing", "thresholds": {}}
        database.save_event(event)

        result = api._metric_thresholds(event["id"], "accessibility")

        assert result == {"warning": None, "critical": None}

    def test_direct_metric_key_takes_priority_over_utilization_fallback(self, api, sample_event):
        event = {**sample_event, "id": "threshold-availability",
                 "thresholds": {"availability_critical": 90}}
        database.save_event(event)

        result = api._metric_thresholds(event["id"], "availability")

        assert result == {"warning": None, "critical": {"value": 90, "unit": "%"}}


class TestKpiOverview:
    def test_overview_devolve_todas_as_metricas_na_mesma_grade(self, api, sample_event):
        event = _twin_sites_event(sample_event, "overview-common-grid")
        database.save_event(event)
        first = "2026-08-19T12:00:00Z"
        second = "2026-08-19T12:05:00Z"
        _insert_site_kpi(
            event["id"], "725483", "4G", 98.0, first, metric="accessibility")
        _insert_site_kpi(
            event["id"], "725483", "4G", 97.0, second, metric="availability")

        result = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "4G", minutes=0)

        assert result["ok"] is True
        assert result["labels"] == [first, second]
        assert len(result["metrics"]) == 9
        assert all(
            len(values) == len(result["labels"])
            for values in result["metrics"].values()
        )
        assert "ran_rtt" not in result["metrics"]
        assert "terrestrial_rtt" not in result["metrics"]

    def test_metrica_sem_ponto_vem_como_null_e_nao_desloca_a_grade(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "overview-null-gap")
        database.save_event(event)
        first = "2026-08-19T12:00:00Z"
        second = "2026-08-19T12:05:00Z"
        _insert_site_kpi(
            event["id"], "725483", "4G", 91.0, first, metric="availability")
        _insert_site_kpi(
            event["id"], "725483", "4G", 93.0, second, metric="availability")
        _insert_site_kpi(
            event["id"], "725483", "4G", 2.5, second, metric="drop_rate")

        result = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "4G", minutes=0)

        assert result["metrics"]["availability"] == [91.0, 93.0]
        assert result["metrics"]["drop_rate"] == [None, 2.5]

    def test_overview_por_cluster_expande_os_membros(self, api, sample_event):
        extra_site = {
            "id": "SR-EXTRA", "name": "SR-EXTRA", "lat": -23.59, "lng": -46.68,
            "is_event_site": True, "cells": _cells("4G-SR-EXTRA", 2),
        }
        event = _event_with_clusters(
            sample_event,
            [{"id": "sul", "name": "Sul", "site_ids": ["SR-SPPNB2", "SR-EXTRA"]}],
            event_id="overview-cluster",
            extra_sites=[extra_site],
        )
        database.save_event(event)
        timestamp = "2026-08-19T12:00:00Z"
        _insert_site_kpi(
            event["id"], "SR-SPPNB2", "4G", 10.0, timestamp,
            metric="throughput_dl")
        _insert_site_kpi(
            event["id"], "SR-EXTRA", "4G", 20.0, timestamp,
            metric="throughput_dl")

        result = api.get_kpi_overview(
            event["id"], "cluster", "sul", "4G", minutes=0)

        assert result["ok"] is True
        assert result["scope"] == "cluster"
        # Fase 3: gravado em Mbit/s, servido em bit/s. A conversão acontece na
        # leitura, antes da soma do cluster — 10+20 Mbit/s viram 30 Mbit/s em
        # bit/s, e não uma soma de bases diferentes.
        assert result["metrics"]["throughput_dl"] == [30_000_000.0]

    def test_grade_4g_tem_nove_paineis_sem_rtt(self, api, sample_event):
        database.save_event(sample_event)

        result = api.get_kpi_overview(
            sample_event["id"], "site", "SR-SPPNB2", "4G", minutes=0)

        assert list(result["metrics"]) == [
            "accessibility", "availability", "drop_rate", "utilization_dl",
            "utilization_ul", "interference_ul", "throughput_dl",
            "throughput_ul", "user_count",
        ]

    def test_grade_5g_tem_nove_paineis_e_cobre_treze_metricas(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "overview-5g-composition")
        database.save_event(event)

        result = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "5G", minutes=0)

        assert result["ok"] is True
        assert set(result["metrics"]) == {
            "accessibility", "drop_rate", "user_count", "availability",
            "interference_ul", "utilization_dl", "utilization_ul",
            "throughput_dl", "throughput_ul", "traffic_volume_dl_sa",
            "traffic_volume_ul_sa", "traffic_volume_dl_nsa",
            "traffic_volume_ul_nsa",
        }
        assert len(result["metrics"]) == 13

    def test_overview_reports_base_unit_for_paired_panels(self, api, sample_event):
        """Fase 3: painel pareado só pode ter eixo único se as duas séries batem.

        Antes da Fase 1 o UL do 5G declarava "unidade OSS pendente" e o
        cabeçalho caía no rótulo "DL / UL"; com a unidade canônica os pares
        passam a coincidir, o que é o pré-requisito do B3 na Fase 4.
        """
        event = _twin_sites_event(sample_event, "overview-5g-unidades")
        database.save_event(event)

        result = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "5G", minutes=0)

        units = result["units"]
        assert units["throughput_dl"] == units["throughput_ul"] == "bit/s"
        assert units["traffic_volume_dl_sa"] == units["traffic_volume_dl_nsa"] == "bit"
        assert units["utilization_dl"] == units["utilization_ul"] == "%"

    def test_overview_reports_reason_per_metric(self, api, sample_event):
        """B6: painel vazio por falta de tráfego não é painel sem coleta.

        Com coleta na janela, a métrica sem nenhum ponto é a que ficou
        indefinida — denominador zero, o caso normal do SA no 5G.
        """
        event = _twin_sites_event(sample_event, "overview-reasons")
        database.save_event(event)
        _insert_site_kpi(
            event["id"], "1774059", "5G_NRDUCELL", 42.0, metric="user_count")

        result = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "5G", minutes=0)

        assert result["reasons"]["user_count"] == "ok"
        assert result["reasons"]["traffic_volume_dl_sa"] == "no_traffic"

    def test_overview_without_any_collection_reports_no_data(
            self, api, sample_event):
        """Sem nenhuma linha na janela, nada é "sem tráfego": não houve coleta."""
        event = _twin_sites_event(sample_event, "overview-sem-coleta")
        database.save_event(event)

        result = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "5G", minutes=0)

        assert result["labels"] == []
        assert set(result["reasons"].values()) == {"no_data"}

    def test_overview_4g_and_5g_volumes_report_the_same_base_unit(
            self, api, sample_event):
        """B1: a mesma grandeza deixa de sair em bases diferentes por tecnologia."""
        event = _twin_sites_event(sample_event, "overview-unidade-comum")
        database.save_event(event)

        quatro_g = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "4G", minutes=0)
        cinco_g = api.get_kpi_overview(
            event["id"], "site", "SPSMG7", "5G", minutes=0)

        assert quatro_g["units"]["throughput_dl"] == cinco_g["units"]["throughput_dl"]


class TestKpiOverviewMulti:
    """Comparação de vários escopos (clusters e/ou sites) na visão geral."""

    def test_dois_clusters_saem_na_mesma_grade_temporal(self, api, sample_event):
        extra_site = {
            "id": "SR-EXTRA", "name": "SR-EXTRA", "lat": -23.59, "lng": -46.68,
            "is_event_site": True, "cells": _cells("4G-SR-EXTRA", 2),
        }
        event = _event_with_clusters(
            sample_event,
            [
                {"id": "sul", "name": "Sul", "site_ids": ["SR-SPPNB2"]},
                {"id": "oeste", "name": "Oeste", "site_ids": ["SR-EXTRA"]},
            ],
            event_id="overview-multi-clusters",
            extra_sites=[extra_site],
        )
        database.save_event(event)
        first = "2026-08-19T12:00:00Z"
        second = "2026-08-19T12:05:00Z"
        # Cada cluster tem ponto em um minuto diferente: a grade tem de unir os
        # dois e preencher o buraco com None, não deslocar a série do vizinho.
        _insert_site_kpi(
            event["id"], "SR-SPPNB2", "4G", 10.0, first, metric="throughput_dl")
        _insert_site_kpi(
            event["id"], "SR-EXTRA", "4G", 20.0, second, metric="throughput_dl")

        result = api.get_kpi_overview_multi(
            event["id"],
            [{"scope": "cluster", "scope_id": "sul"},
             {"scope": "cluster", "scope_id": "oeste"}],
            "4G",
            minutes=0,
        )

        assert result["ok"] is True
        assert result["labels"] == [first, second]
        assert [item["scope_id"] for item in result["series"]] == ["sul", "oeste"]
        assert result["series"][0]["metrics"]["throughput_dl"] == [10_000_000.0, None]
        assert result["series"][1]["metrics"]["throughput_dl"] == [None, 20_000_000.0]

    def test_reason_consolidado_entre_escopos_usa_o_melhor(self, api, sample_event):
        """B6: um escopo com tráfego já basta para o painel não estar vazio."""
        extra_site = {
            "id": "SR-EXTRA", "name": "SR-EXTRA", "lat": -23.59, "lng": -46.68,
            "is_event_site": True, "cells": _cells("4G-SR-EXTRA", 2),
        }
        event = _event_with_clusters(
            sample_event,
            [
                {"id": "sul", "name": "Sul", "site_ids": ["SR-SPPNB2"]},
                {"id": "oeste", "name": "Oeste", "site_ids": ["SR-EXTRA"]},
            ],
            event_id="overview-multi-reasons",
            extra_sites=[extra_site],
        )
        database.save_event(event)
        _insert_site_kpi(
            event["id"], "SR-SPPNB2", "4G", 10.0, metric="throughput_dl")

        result = api.get_kpi_overview_multi(
            event["id"],
            [{"scope": "cluster", "scope_id": "sul"},
             {"scope": "cluster", "scope_id": "oeste"}],
            "4G",
            minutes=0,
        )

        # "sul" tem ponto e "oeste" não tem nada: o painel continua com dados.
        assert result["reasons"]["throughput_dl"] == "ok"
        # Nenhum dos dois tem accessibility, mas houve coleta na janela.
        assert result["reasons"]["accessibility"] == "no_traffic"

    def test_mistura_cluster_e_site_preservando_a_ordem_pedida(self, api, sample_event):
        event = _event_with_clusters(
            sample_event,
            [{"id": "sul", "name": "Sul", "site_ids": ["SR-SPPNB2"]}],
            event_id="overview-multi-mixed",
        )
        database.save_event(event)

        result = api.get_kpi_overview_multi(
            event["id"],
            [{"scope": "site", "scope_id": "SR-SPPNB2"},
             {"scope": "cluster", "scope_id": "sul"}],
            "4G",
            minutes=0,
        )

        assert result["ok"] is True
        assert [(item["scope"], item["scope_id"]) for item in result["series"]] == [
            ("site", "SR-SPPNB2"), ("cluster", "sul"),
        ]

    def test_todas_as_series_cobrem_as_mesmas_metricas_do_painel(
            self, api, sample_event):
        event = _twin_sites_event(sample_event, "overview-multi-5g")
        database.save_event(event)

        result = api.get_kpi_overview_multi(
            event["id"],
            [{"scope": "site", "scope_id": "SPSMG7"},
             {"scope": "site", "scope_id": "SPSMH1"}],
            "5G",
            minutes=0,
        )

        assert result["ok"] is True
        for item in result["series"]:
            assert set(item["metrics"]) == set(api_module.KPI_OVERVIEW_METRICS["5G"])
            assert all(
                len(values) == len(result["labels"])
                for values in item["metrics"].values()
            )

    def test_escopo_repetido_conta_uma_vez_so(self, api, sample_event):
        database.save_event(sample_event)

        result = api.get_kpi_overview_multi(
            sample_event["id"],
            [{"scope": "site", "scope_id": "SR-SPPNB2"},
             {"scope": "site", "scope_id": "SR-SPPNB2"}],
            "4G",
            minutes=0,
        )

        assert result["ok"] is True
        assert len(result["series"]) == 1

    def test_recusa_lista_vazia_e_escopo_invalido(self, api, sample_event):
        database.save_event(sample_event)

        assert api.get_kpi_overview_multi(sample_event["id"], [], "4G")["ok"] is False
        assert api.get_kpi_overview_multi(
            sample_event["id"], [{"scope": "celula", "scope_id": "x"}], "4G",
        )["ok"] is False

    def test_aceita_mais_de_oito_escopos(self, api, sample_event, monkeypatch):
        database.save_event(sample_event)
        scopes = [
            {"scope": "site", "scope_id": f"SITE-{index}"}
            for index in range(12)
        ]

        def overview_ok(event_id, scope, scope_id, family, minutes):
            metrics = api_module.KPI_OVERVIEW_METRICS[family]
            return {
                "ok": True,
                "labels": ["2026-08-20T12:00:00"],
                "metrics": {metric: [1.0] for metric in metrics},
                "units": {},
                "thresholds": {},
                "reasons": {metric: "ok" for metric in metrics},
            }

        monkeypatch.setattr(api, "get_kpi_overview", overview_ok)

        result = api.get_kpi_overview_multi(sample_event["id"], scopes, "4G")

        assert result["ok"] is True
        assert len(result["series"]) == 12

    def test_familia_invalida_nao_chega_a_consultar(self, api, sample_event):
        database.save_event(sample_event)

        result = api.get_kpi_overview_multi(
            sample_event["id"], [{"scope": "site", "scope_id": "SR-SPPNB2"}], "6G")

        assert result["ok"] is False
        assert result["series"] == []
