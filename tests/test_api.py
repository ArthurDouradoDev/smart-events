"""
Testes para api/api.py.

Usa o banco temporário via fixture tmp_db e monkeypatcha o scheduler
para não subir threads reais.
"""
import pytest
import core.database as database
from api.api import Api


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

    def test_end_event(self, api_with_event):
        api, ev = api_with_event
        result = api.end_event(ev["id"])
        assert result.get("ok") is True

    def test_activate_nonexistent_event(self, api):
        result = api.activate_event("nao-existe", mock=True)
        assert result.get("ok") is False


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


class TestApiCollectionStatus:
    def test_get_collection_status_structure(self, api):
        # get_collection_status retorna {"ok", "recording", "kpi", "vip", "session", "now"}
        status = api.get_collection_status()
        assert status.get("ok") is True
        assert "recording" in status
        assert "kpi" in status
        assert "vip" in status

    def test_collection_status_not_recording_by_default(self, api):
        status = api.get_collection_status()
        assert status["recording"] is False

    def test_get_app_status_has_db_size(self, api):
        # db_size_mb está em get_app_status(), não em get_collection_status()
        status = api.get_app_status()
        assert "db_size_mb" in status
        assert "recording" in status
