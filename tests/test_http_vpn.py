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
        session = http_col_sp._build_session("monitoring")
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
        session = http_col_rj._build_session("monitoring")
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
        result = http_col_sp.collect_kpis()
        assert result.state in {"data", "empty", "partial"}
        assert isinstance(result.measurements, list)

    def test_collect_kpis_structure(self, http_col_sp, event_in_db):
        kpis = http_col_sp.collect_kpis().measurements
        if kpis:
            first = kpis[0]
            assert first.get("event_id")
            assert first.get("metric")
            assert first.get("value") is not None


class TestHttpCollectVips:
    def test_collect_vips_returns_list(self, http_col_sp, event_in_db):
        """Coleta VIP real — OK retornar lista vazia se nenhum VIP configurado no evento."""
        result = http_col_sp.collect_vips()
        assert result.state in {"data", "empty", "partial"}
        assert isinstance(result.measurements, list)


class TestHttpCollectAlarms:
    def test_collect_alarms_returns_list(self, http_col_sp, event_in_db):
        """Coleta de alarmes real — espelha o teste ao vivo de 30/06.

        Deve retornar uma lista com ≥1 alarme e conter APENAS os tipos filtrados
        (VSWR + Cell Unavailable, o default de oss.alarm_filter)."""
        result = http_col_sp.collect_alarms()
        assert result.state in {"data", "empty", "partial"}
        alarms = result.measurements
        assert len(alarms) >= 1, "nenhum alarme retornado — filtro/sessão?"
        allowed = {"RF Unit VSWR Threshold Crossed", "Cell Unavailable"}
        names = {a.get("alarm_name") for a in alarms}
        assert names.issubset(allowed), f"tipos fora do filtro: {names - allowed}"
        csns = [a["csn"] for a in alarms]
        assert len(csns) == len(set(csns)), "csn duplicado (dedup falhou)"


class TestHttpCsrfHeaders:
    def test_roarand_header_matches_cookie(self, http_col_sp):
        """Anti-CSRF: header roarand deve ecoar o cookie roarand (double-submit)."""
        session = http_col_sp._build_session("monitoring")
        cookie_val = session.cookies.get("roarand")
        header_val = session.headers.get("roarand")
        if cookie_val:
            assert header_val == cookie_val, (
                "Header roarand não está ecoando o cookie roarand — coleta RJ falhará"
            )

    def test_origin_referer_set(self, http_col_sp):
        session = http_col_sp._build_session("monitoring")
        assert "Origin" in session.headers or "Referer" in session.headers
