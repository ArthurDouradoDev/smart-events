"""
Testes unitários para core/collector.py.

Cobre: parsers de RSRP/RSRQ, detecção de célula por evento,
MockCollector, CsvCollector e factory build_collector.
Nenhum teste aqui requer VPN — ver test_http_vpn.py para integração real.
"""
import json
from datetime import datetime, timedelta

import pytest
import core.database as db
from core.collector import (
    BaseCollector,
    MockCollector,
    CsvCollector,
    HttpCollector,
    NullCollector,
    build_collector,
)
from core.session_renew import _browser_profile_path
from core.collection_result import CollectionResult


@pytest.fixture
def patched_db(monkeypatch):
    """Isola collectors do DB real para testes puramente unitários."""
    monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])


def test_dump_raw_is_opt_in_and_never_persists_session_tokens(sample_event, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
    monkeypatch.setattr("core.collector.credentials.data_dir", lambda: tmp_path)
    collector = HttpCollector(sample_event, "https://10.220.30.9:31943")
    payload = {"data": [{"value": 1}], "roarand": "secret", "bspsession": "secret"}

    assert collector._dump_raw(payload, "monitoring") is None
    assert not (tmp_path / "diagnostics").exists()

    monkeypatch.setenv("SMARTEVENTS_CAPTURE_RAW", "1")
    path = collector._dump_raw(payload, "monitoring")

    assert path and path.exists()
    dumped = path.read_text(encoding="utf-8").lower()
    assert "roarand" not in dumped and "bspsession" not in dumped
    assert json.loads(dumped) == {"data": [{"value": 1}]}


def test_backoff_e_captcha_sao_isolados_por_host(sample_event, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
    HttpCollector.reset_interactive_state()
    sp = HttpCollector(sample_event, "https://10.220.50.9:31943")
    curitiba_event = {**sample_event, "id": "curitiba",
                      "oss": {**sample_event["oss"], "region": "OUTRAS"}}
    curitiba = HttpCollector(curitiba_event, "https://10.220.30.9:31943")

    sp_key = sp._module_state_key("monitoring")
    curitiba_key = curitiba._module_state_key("monitoring")
    sp._engage_backoff("monitoring")
    HttpCollector._needs_interactive[sp_key] = "token-sp"
    HttpCollector._interactive_cooldown_until[sp_key] = datetime.utcnow() + timedelta(minutes=5)

    assert sp_key in HttpCollector._renew_backoff_until
    assert curitiba_key not in HttpCollector._renew_backoff_until
    assert HttpCollector.interactive_modules_for(sp.base_url) == {"monitoring"}
    assert HttpCollector.interactive_modules_for(curitiba.base_url) == set()
    assert HttpCollector._interactive_cooldown_until.get(curitiba_key) is None
    HttpCollector.reset_interactive_state()


def test_session_files_e_browser_profiles_sao_separados_por_host(
        sample_event, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
    monkeypatch.setattr(db, "BASE_DIR", tmp_path)
    sp_url = "https://10.220.50.9:31943"
    curitiba_url = "https://10.220.30.9:31943"

    sp_session = HttpCollector._resolve_session_file(sp_url)
    curitiba_session = HttpCollector._resolve_session_file(curitiba_url)
    sp_profile = _browser_profile_path(sp_session, sp_url)
    curitiba_profile = _browser_profile_path(curitiba_session, curitiba_url)

    assert sp_session != curitiba_session
    assert sp_profile != curitiba_profile
    assert sp_profile.name == "browser_profile_10_220_50_9"
    assert curitiba_profile.name == "browser_profile_10_220_30_9"


# ── _extract_rsrp_rsrq ────────────────────────────────────────────────

class TestExtractRsrpRsrq:
    """
    Formato iManager: "rsrpResult: ---- 0x30(48)" → rsrp = 48 - 140 = -92
                      "rsrqResult: ---- 0x15(21)" → rsrq = 21/2 - 19.5 = -9.0
    """

    def test_rsrp_normal(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rsrp, rsrq = c._extract_rsrp_rsrq("rsrpResult: ---- 0x30(48)\nrsrqResult: ---- 0x15(21)")
        assert rsrp == pytest.approx(-92.0)
        assert rsrq == pytest.approx(-9.0)

    def test_rsrp_boundary_zero(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rsrp, _ = c._extract_rsrp_rsrq("rsrpResult: ---- 0x00(0)\nrsrqResult: ---- 0x1b(27)")
        assert rsrp == pytest.approx(-140.0)

    def test_rsrp_max(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rsrp, _ = c._extract_rsrp_rsrq("rsrpResult: ---- 0x61(97)\nrsrqResult: ---- 0x27(39)")
        assert rsrp == pytest.approx(-43.0)

    def test_missing_rsrp(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rsrp, rsrq = c._extract_rsrp_rsrq("rsrqResult: ---- 0x15(21)")
        assert rsrp is None
        assert rsrq == pytest.approx(-9.0)

    def test_missing_both(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rsrp, rsrq = c._extract_rsrp_rsrq("sem dados aqui")
        assert rsrp is None
        assert rsrq is None


# ── _cell_in_event ────────────────────────────────────────────────────

class TestCellInEvent:
    """Quatro estratégias de matching de célula."""

    def _col(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        return MockCollector(sample_event)

    def test_exact_match(self, sample_event, monkeypatch):
        c = self._col(sample_event, monkeypatch)
        assert c._cell_in_event("SR-SPPNB2_1") is True

    def test_obj_no_match(self, sample_event, monkeypatch):
        c = self._col(sample_event, monkeypatch)
        assert c._cell_in_event("211") is True  # obj_no da célula 1

    def test_site_prefix_match(self, sample_event, monkeypatch):
        c = self._col(sample_event, monkeypatch)
        # Site prefix SR-SPPNB2 está no evento
        assert c._cell_in_event("SR-SPPNB2-Cell-1") is True

    def test_no_match(self, sample_event, monkeypatch):
        c = self._col(sample_event, monkeypatch)
        assert c._cell_in_event("SR-OUTROSITE_1") is False

    def test_empty_cell_id(self, sample_event, monkeypatch):
        c = self._col(sample_event, monkeypatch)
        assert c._cell_in_event("") is False


# ── MockCollector ─────────────────────────────────────────────────────

class TestMockCollector:
    def test_collect_kpis_returns_list_of_dicts(self, event_in_db, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        result = c.collect_kpis()
        assert isinstance(result, CollectionResult)
        assert result.state == "data"
        assert len(result.measurements) > 0
        first = result.measurements[0]
        assert isinstance(first, dict)
        assert "event_id" in first
        assert "metric" in first
        assert "value" in first
        assert first["event_id"] == sample_event["id"]

    def test_collect_kpis_covers_expected_metrics(self, event_in_db, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        result = c.collect_kpis()
        metrics = {k["metric"] for k in result.measurements}
        # Nomes reais do MockCollector — mapeados conforme KPI_COLUMN_MAP
        expected = {"utilization_dl", "traffic_volume_dl", "traffic_volume_ul", "accessibility"}
        assert expected.issubset(metrics)

    def test_collect_vips_empty_when_no_vips(self, event_in_db, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        assert c.collect_vips().state == "empty"

    def test_collect_vips_with_vips(self, event_in_db, sample_event, monkeypatch):
        fake_vips = [{"id": "vip-teste", "name": "Teste VIP", "task_id": "T001"}]
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: fake_vips)
        c = MockCollector(sample_event)
        result = c.collect_vips()
        assert isinstance(result, CollectionResult)


# ── NullCollector ─────────────────────────────────────────────────────

class TestNullCollector:
    def test_collect_kpis_empty(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = NullCollector(sample_event)
        assert c.collect_kpis().state == "empty"

    def test_collect_vips_empty(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = NullCollector(sample_event)
        assert c.collect_vips().state == "empty"


# ── CsvCollector ──────────────────────────────────────────────────────

class TestCsvCollector:
    def test_collect_kpis_from_csv(self, event_in_db, sample_event, csv_kpi_dir, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        # CsvCollector(event_config, import_folder) — dois argumentos posicionais
        c = CsvCollector(sample_event, str(csv_kpi_dir))
        result = c.collect_kpis()
        kpis = result.measurements
        assert result.state == "data"
        assert len(kpis) > 0
        metrics = {k["metric"] for k in kpis}
        # "DL PRB USAGE" → "utilization_dl" conforme KPI_COLUMN_MAP
        assert "utilization_dl" in metrics

    def test_collect_vips_returns_empty(self, event_in_db, sample_event, csv_kpi_dir, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = CsvCollector(sample_event, str(csv_kpi_dir))
        assert c.collect_vips().state == "empty"

    def test_csv_empty_dir(self, event_in_db, sample_event, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        empty = tmp_path / "empty_imports"
        empty.mkdir()
        c = CsvCollector(sample_event, str(empty))
        assert c.collect_kpis().state == "empty"


# ── build_collector factory ────────────────────────────────────────────

class TestBuildCollector:
    def test_mock_flag_returns_mock_collector(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = build_collector(sample_event, mock=True)
        assert isinstance(c, MockCollector)

    def test_import_folder_returns_csv_collector(self, sample_event, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        (tmp_path / "imports").mkdir()
        ev = {**sample_event, "oss": {**sample_event["oss"], "import_folder": str(tmp_path / "imports")}}
        c = build_collector(ev, mock=False)
        assert isinstance(c, CsvCollector)

    def test_no_import_folder_returns_http_collector(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        ev = {**sample_event, "oss": {**sample_event["oss"], "import_folder": ""}}
        c = build_collector(ev, mock=False)
        assert isinstance(c, HttpCollector)

    def test_nonexistent_import_folder_falls_back_to_http(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        ev = {**sample_event, "oss": {**sample_event["oss"], "import_folder": "/caminho/inexistente"}}
        c = build_collector(ev, mock=False)
        assert isinstance(c, HttpCollector)
