"""Testes para os dataclasses em core/models.py."""
import pytest
from core.models import (
    Cell, Site, VIP, Thresholds, OssConfig, EventConfig,
    KpiMeasurement, VipMeasurement, Alert,
)


class TestCell:
    def test_defaults(self):
        c = Cell(id="A_1", azimuth=0)
        assert c.beamwidth == 120.0
        assert c.status == "unknown"
        assert c.tech is None
        assert c.frequency is None

    def test_custom_values(self):
        c = Cell(id="A_1", azimuth=90, beamwidth=65.0, status="active", tech="LTE", frequency=1800)
        assert c.beamwidth == 65.0
        assert c.tech == "LTE"
        assert c.frequency == 1800


class TestSite:
    def test_defaults(self):
        s = Site(id="SR-001", name="Site A", lat=-23.0, lng=-46.0)
        assert s.cells == []
        assert s.is_event_site is True
        assert s.status == "unknown"

    def test_with_cells(self):
        cells = [Cell(id="SR-001_1", azimuth=0), Cell(id="SR-001_2", azimuth=120)]
        s = Site(id="SR-001", name="Site A", lat=-23.0, lng=-46.0, cells=cells)
        assert len(s.cells) == 2


class TestVIP:
    def test_defaults(self):
        v = VIP(name="Fulano")
        assert v.task_id is None
        assert v.serving_cell is None
        assert v.rsrp is None
        assert v.rsrq is None
        assert v.in_event is False
        assert v.status == "unknown"
        assert v.oss is None

    def test_with_measurements(self):
        v = VIP(name="Fulano", task_id="T123", rsrp=-95.0, rsrq=-10.0, in_event=True)
        assert v.rsrp == -95.0
        assert v.in_event is True


class TestThresholds:
    def test_defaults(self):
        t = Thresholds()
        assert t.rsrp_warning == -100.0
        assert t.rsrp_critical == -110.0
        assert t.rsrq_warning == -12.0
        assert t.rsrq_critical == -15.0
        assert t.utilization_warning == 80.0
        assert t.utilization_critical == 95.0
        assert t.availability_critical == 95.0

    def test_custom(self):
        t = Thresholds(rsrp_warning=-95.0, rsrp_critical=-105.0)
        assert t.rsrp_warning == -95.0
        assert t.rsrp_critical == -105.0


class TestOssConfig:
    def test_defaults(self):
        o = OssConfig()
        assert o.base_url == ""
        assert o.region == ""
        assert o.import_folder == ""

    def test_custom(self):
        o = OssConfig(base_url="https://10.220.50.9:31943", region="SP", import_folder="/exports")
        assert o.region == "SP"


class TestAlert:
    def test_defaults(self):
        a = Alert(
            id="1", event_id="ev1", level="warning", severity="WARNING",
            site_id="SR-001", cell_id="SR-001_1",
            message="PRB alto", timestamp="2026-06-01T10:00:00Z"
        )
        assert a.acknowledged is False
