"""
Testes do nível Cliente → Regional e da resolução/gerência de credenciais.

Cobrem:
  - resolução de base_url pelo catálogo (cliente, regional);
  - precedência das credenciais (override por regional > compartilhada do cliente > vazio);
  - status sem vazar senha;
  - o gatilho de UI: Api.activate_event devolve needs_credentials quando falta acesso
    e passa a ativar depois que a credencial é salva.
"""
import pytest

import core.credentials as credentials


@pytest.fixture
def tmp_creds(tmp_path, monkeypatch):
    """Aponta o credentials.json para um arquivo temporário (isola dos dados reais)."""
    f = tmp_path / "credentials.json"
    monkeypatch.setattr(credentials, "credentials_file", lambda: f)
    return f


# ── Catálogo rico (formato sincronizado do servidor) ─────────────────

def test_catalogo_rico_resolve_e_summary(tmp_path, monkeypatch):
    """O cache sincronizado usa {cliente:{name,logo,regionais:{...}}}; resolve_base_url e
    clientes_summary devem entender esse formato (e o simples, por compatibilidade)."""
    import json as _json
    f = tmp_path / "clientes.json"
    f.write_text(_json.dumps({
        "TIM": {"name": "TIM", "logo": "tim.png", "regionais": {"SP": "https://a:1", "RJ": "https://b:2"}},
        "Vivo": {"SP": "https://c:3"},  # formato simples (legado)
    }), encoding="utf-8")
    monkeypatch.setattr(credentials, "clientes_file", lambda: f)

    assert credentials.resolve_base_url({"cliente": "TIM", "region": "RJ"}) == "https://b:2"
    assert credentials.resolve_base_url({"cliente": "Vivo", "region": "SP"}) == "https://c:3"

    summary = credentials.clientes_summary()
    assert summary["TIM"]["logo"] == "tim.png"
    assert summary["TIM"]["regionais"] == ["RJ", "SP"]
    assert summary["Vivo"]["regionais"] == ["SP"]


# ── Catálogo / base_url ──────────────────────────────────────────────

def test_resolve_base_url_pelo_catalogo():
    assert credentials.resolve_base_url({"cliente": "TIM", "region": "SP"}) == "https://10.220.50.9:31943"
    assert credentials.resolve_base_url({"cliente": "TIM", "region": "RJ"}) == "https://10.220.30.9:31943"


def test_resolve_base_url_override_explicito():
    oss = {"cliente": "TIM", "region": "SP", "base_url": "https://1.2.3.4:9999/"}
    assert credentials.resolve_base_url(oss) == "https://1.2.3.4:9999"


def test_resolve_base_url_desconhecido_cai_no_default():
    assert credentials.resolve_base_url({"cliente": "X", "region": "ZZ"}) == credentials._DEFAULT_BASE_URL


# ── Precedência das credenciais ──────────────────────────────────────

def test_sem_credencial_retorna_vazio(tmp_creds):
    assert credentials.resolve_credentials("TIM", "SP") == ("", "")
    assert credentials.has_credentials("TIM", "SP") is False


def test_compartilhada_vale_para_todas_regionais_do_cliente(tmp_creds):
    credentials.save_shared("TIM", "u_shared", "p_shared")
    assert credentials.resolve_credentials("TIM", "SP") == ("u_shared", "p_shared")
    assert credentials.resolve_credentials("TIM", "RJ") == ("u_shared", "p_shared")
    # Escopo por cliente: outro cliente não herda a compartilhada do TIM.
    assert credentials.resolve_credentials("Vivo", "SP") == ("", "")


def test_override_por_regional_tem_precedencia(tmp_creds):
    credentials.save_shared("TIM", "u_shared", "p_shared")
    credentials.save_credential("TIM", "RJ", "u_rj", "p_rj")
    assert credentials.resolve_credentials("TIM", "RJ") == ("u_rj", "p_rj")
    assert credentials.resolve_credentials("TIM", "SP") == ("u_shared", "p_shared")


def test_delete_volta_para_compartilhada(tmp_creds):
    credentials.save_shared("TIM", "u_shared", "p_shared")
    credentials.save_credential("TIM", "RJ", "u_rj", "p_rj")
    credentials.delete_credential("TIM", "RJ")
    assert credentials.resolve_credentials("TIM", "RJ") == ("u_shared", "p_shared")


def test_status_for_nao_vaza_senha(tmp_creds):
    credentials.save_credential("TIM", "SP", "u_sp", "p_sp")
    st = credentials.status_for("TIM")
    # Nenhum campo "password" em lugar nenhum do status.
    assert "password" not in st["shared"]
    sp = next(r for r in st["regionais"] if r["region"] == "SP")
    assert sp["configured"] is True and sp["uses_shared"] is False and sp["username"] == "u_sp"
    assert "password" not in sp


# ── Gatilho de UI: Api.activate_event ────────────────────────────────

def test_activate_event_pede_credenciais_e_ativa_apos_salvar(tmp_db, monkeypatch, tmp_creds, sample_event):
    """Sem credencial → needs_credentials (abre o modal). Após salvar → o gate libera."""
    import core.database as database
    from api.api import Api
    from core.scheduler import scheduler

    # Evento HTTP do cliente TIM, regional SP (sem base_url/import_folder → coleta HTTP).
    ev = {**sample_event, "oss": {"cliente": "TIM", "region": "SP", "base_url": "", "import_folder": ""}}
    database.save_event(ev)

    api = Api()

    res = api.activate_event(ev["id"])
    assert res["ok"] is False and res["needs_credentials"] is True
    assert res["cliente"] == "TIM" and res["region"] == "SP"
    assert res["base_url"] == "https://10.220.50.9:31943"

    # Salva a credencial (como o modal faria) e não inicia coleta de verdade.
    assert api.save_credentials("TIM", "SP", "u", "p")["ok"] is True
    monkeypatch.setattr(scheduler, "start", lambda *a, **k: None)
    monkeypatch.setattr(database, "sync_vips_from_server", lambda *a, **k: {})

    res2 = api.activate_event(ev["id"])
    assert res2["ok"] is True
    assert "needs_credentials" not in res2


def test_activate_event_legado_sem_cliente_fixa_e_ativa(tmp_db, monkeypatch, tmp_creds, sample_event):
    """Evento legado (region sem cliente): needs_credentials com cliente=''. Após o operador
    escolher o cliente e salvar, a re-ativação com `cliente` fixa oss.cliente e ativa."""
    import core.database as database
    from api.api import Api
    from core.scheduler import scheduler

    # Evento sem oss.cliente (criado antes do nível Cliente).
    ev = {**sample_event, "oss": {"region": "RJ", "base_url": "", "import_folder": ""}}
    database.save_event(ev)
    api = Api()

    res = api.activate_event(ev["id"])
    assert res["ok"] is False and res["needs_credentials"] is True
    assert res["cliente"] == "" and res["region"] == "RJ"

    # Operador escolheu TIM no modal e salvou a credencial dessa regional.
    assert api.save_credentials("TIM", "RJ", "u", "p")["ok"] is True
    monkeypatch.setattr(scheduler, "start", lambda *a, **k: None)
    monkeypatch.setattr(database, "sync_vips_from_server", lambda *a, **k: {})

    res2 = api.activate_event(ev["id"], cliente="TIM")
    assert res2["ok"] is True
    # O cliente foi persistido no evento.
    assert database.get_event(ev["id"])["oss"]["cliente"] == "TIM"


def test_get_event_vips_inclui_legados_sem_cliente(tmp_db, sample_event):
    """VIPs sem cliente (legado) devem continuar aparecendo; só VIPs de cliente DIFERENTE
    (ou de outra regional) são excluídos. Evita o regressão de coleta vazia."""
    import core.database as database
    ev = {**sample_event, "id": "e-rj", "oss": {"cliente": "TIM", "region": "RJ"}}
    database.save_event(ev)
    database.save_vip({"id": "v1", "name": "Legado", "oss": "RJ"})                    # cliente None
    database.save_vip({"id": "v2", "name": "TimVip", "oss": "RJ", "cliente": "TIM"})
    database.save_vip({"id": "v3", "name": "VivoVip", "oss": "RJ", "cliente": "Vivo"})  # outro cliente
    database.save_vip({"id": "v4", "name": "SpVip", "oss": "SP"})                      # outra regional

    names = sorted(v["name"] for v in database.get_event_vips("e-rj"))
    assert names == ["Legado", "TimVip"]


def test_activate_event_mock_nao_pede_credenciais(tmp_db, monkeypatch, tmp_creds, sample_event):
    """Modo mock não exige credencial (não há coleta HTTP)."""
    import core.database as database
    from api.api import Api
    from core.scheduler import scheduler

    ev = {**sample_event, "oss": {"cliente": "TIM", "region": "SP", "base_url": "", "import_folder": ""}}
    database.save_event(ev)
    monkeypatch.setattr(scheduler, "start", lambda *a, **k: None)
    monkeypatch.setattr(database, "sync_vips_from_server", lambda *a, **k: {})

    res = Api().activate_event(ev["id"], mock=True)
    assert res["ok"] is True
