"""
Testes de integração VPN — requerem conexão ativa com a VPN do cliente.

Executar com:
    python -m pytest tests/test_http_vpn.py -v --vpn
    python -m pytest tests/test_http_vpn.py -v --vpn --vpn-prompt   # exibe diálogo primeiro

Todos os testes deste módulo são marcados com @pytest.mark.vpn e são
automaticamente pulados quando --vpn não é passado.
"""
import pytest
import core.database as db
from core.collector import HttpCollector, _REGIONAL_BASE_URLS, _DEFAULT_BASE_URL


pytestmark = pytest.mark.vpn


@pytest.fixture
def http_col_sp(event_in_db, sample_event, monkeypatch):
    """HttpCollector apontado para o OSS SP."""
    ev = {**sample_event, "oss": {**sample_event["oss"], "base_url": _DEFAULT_BASE_URL, "region": "SP"}}
    return HttpCollector(ev)


@pytest.fixture
def http_col_rj(event_in_db, sample_event, monkeypatch):
    """HttpCollector apontado para o OSS RJ."""
    ev = {**sample_event, "oss": {**sample_event["oss"],
          "base_url": _REGIONAL_BASE_URLS["RJ"], "region": "RJ"}}
    return HttpCollector(ev)


class TestHttpSessionSP:
    def test_session_file_resolved_sp(self, http_col_sp):
        fname = http_col_sp._resolve_session_file(_DEFAULT_BASE_URL)
        assert "session" in fname
        assert "10_220_30" not in fname  # não deve confundir SP com RJ

    def test_session_valid_sp(self, http_col_sp):
        """Verifica que a sessão SP está ativa (cookie válido, sem redirect SSO)."""
        import requests
        session = http_col_sp._build_session("OSS")
        resp = session.get(
            f"{_DEFAULT_BASE_URL}/rest/plat/smapp/v1/businesstype/NetworkElement",
            verify=False, timeout=15
        )
        assert resp.status_code != 401, "Sessão SP expirada — renovar session.json"
        assert resp.status_code != 403
        is_valid = http_col_sp._check_session_valid(resp, "OSS")
        assert is_valid, f"Sessão SP inválida (redirect SSO?). URL: {resp.url}"


class TestHttpSessionRJ:
    def test_session_file_resolved_rj(self, http_col_rj):
        fname = http_col_rj._resolve_session_file(_REGIONAL_BASE_URLS["RJ"])
        assert "10_220_30" in fname or "session_" in fname

    def test_session_valid_rj(self, http_col_rj):
        """Verifica que a sessão RJ está ativa."""
        import requests
        session = http_col_rj._build_session("OSS")
        resp = session.get(
            f"{_REGIONAL_BASE_URLS['RJ']}/rest/plat/smapp/v1/businesstype/NetworkElement",
            verify=False, timeout=15
        )
        assert resp.status_code != 401, "Sessão RJ expirada — renovar session_10_220_30_9.json"
        is_valid = http_col_rj._check_session_valid(resp, "OSS")
        assert is_valid, f"Sessão RJ inválida (redirect SSO / CSRF?). URL: {resp.url}"


class TestHttpCollectKpis:
    def test_collect_kpis_returns_list(self, http_col_sp, event_in_db):
        """Coleta KPI real do OSS SP — deve retornar lista (pode ser vazia se sem dados)."""
        kpis = http_col_sp.collect_kpis()
        assert isinstance(kpis, list)

    def test_collect_kpis_structure(self, http_col_sp, event_in_db):
        kpis = http_col_sp.collect_kpis()
        if kpis:
            first = kpis[0]
            assert hasattr(first, "event_id")
            assert hasattr(first, "metric")
            assert hasattr(first, "value")
            assert first.value is not None


class TestHttpCollectVips:
    def test_collect_vips_returns_list(self, http_col_sp, event_in_db):
        """Coleta VIP real — OK retornar lista vazia se nenhum VIP configurado no evento."""
        vips = http_col_sp.collect_vips()
        assert isinstance(vips, list)


class TestHttpCsrfHeaders:
    def test_roarand_header_matches_cookie(self, http_col_sp):
        """Anti-CSRF: header roarand deve ecoar o cookie roarand (double-submit)."""
        session = http_col_sp._build_session("OSS")
        cookie_val = session.cookies.get("roarand")
        header_val = session.headers.get("roarand")
        if cookie_val:
            assert header_val == cookie_val, (
                "Header roarand não está ecoando o cookie roarand — coleta RJ falhará"
            )

    def test_origin_referer_set(self, http_col_sp):
        session = http_col_sp._build_session("OSS")
        assert "Origin" in session.headers or "Referer" in session.headers
