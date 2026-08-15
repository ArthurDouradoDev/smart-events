"""
Testes para api/api.py.

Usa o banco temporário via fixture tmp_db e monkeypatcha o scheduler
para não subir threads reais.
"""
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

        assert result["technologies"] == ["5G"]
        assert result["metrics"]
        assert {item["technology"] for item in result["metrics"]} == {"5G"}

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
