import math

import pytest

from core.kpi_formulas import (
    CATALOG, InvalidKpi, NotApplicable, calculate, catalog_for_api,
    check_throughput_floor, definition, sample_size,
    threshold_object, threshold_value,
)


def _counters(definition):
    # Vetor conhecido não nulo para cada contador. Mantém denominadores positivos
    # inclusive nas fórmulas 5G com subtrações.
    return {name: 10.0 for name in definition.required}


# O Granularity Period real das tasks do evento é de 1 minuto: o CSV do OSS traz
# ``Period(minute)=1`` e ``N.Cell.Avail.Dur=60``. O ``period=5`` que a resposta do
# Monitoring devolve não é o GP — ver plano 2026-08-21-001.
GRANULARITY_PERIOD_MIN = 1


@pytest.mark.parametrize("definition", CATALOG, ids=lambda item: f"{item.technology}-{item.id}")
def test_every_catalog_formula_has_a_deterministic_numeric_vector(definition):
    value = calculate(definition, _counters(definition), period=GRANULARITY_PERIOD_MIN)
    assert isinstance(value, float)
    assert math.isfinite(value)


@pytest.mark.parametrize("definition", [item for item in CATALOG if len(item.required) > 1])
def test_missing_required_counter_is_invalid(definition):
    counters = _counters(definition)
    counters.pop(definition.required[0])
    with pytest.raises(NotApplicable, match="contador ausente"):
        calculate(definition, counters, period=GRANULARITY_PERIOD_MIN)


@pytest.mark.parametrize("definition", [item for item in CATALOG if "availability" == item.id])
def test_availability_requires_the_configured_granularity_period(definition):
    with pytest.raises(InvalidKpi, match="period"):
        calculate(definition, _counters(definition), period=None)


def test_not_applicable_is_still_caught_as_invalid_kpi():
    """Todo ``except InvalidKpi`` já escrito continua funcionando."""
    assert issubclass(NotApplicable, InvalidKpi)
    with pytest.raises(InvalidKpi):
        calculate(definition("availability", "5G_NRCELL"), {}, period=1)


@pytest.mark.parametrize("period, expected", [(1, 100.0), (5, 20.0)])
def test_availability_uses_configured_period(period, expected):
    """``N.Cell.Avail.Dur=60`` num GP de 1 min é 100%. Com o ``period=5`` da
    resposta do OSS saía 20% — a linha reta que o painel 5G exibia."""
    value = calculate(definition("availability", "5G_NRCELL"),
                      {"N.Cell.Avail.Dur": 60.0}, period=period)
    assert value == pytest.approx(expected)


def test_4g_availability_reports_zero_for_fully_down_cell():
    value = calculate(definition("availability", "4G"),
                      {"L.Cell.Unavail.Dur.Sys": 60.0, "L.Cell.Unavail.Dur.Manual": 0.0},
                      period=GRANULARITY_PERIOD_MIN)
    assert value == pytest.approx(0.0)


# Vetor real de 5G-SPSMH1-35-DA em 2026-08-21 08:19:00 (ExportMonitoringResult 748).
_CSV_5G_DL = {
    "N.ThpVol.DL": 194425.616, "N.ThpVol.DL.LastSlot": 139501.992,
    "N.ThpTime.DL.RmvLastSlot": 172500.0,
}


def test_5g_throughput_is_in_mbit_per_second():
    value = calculate(definition("throughput_dl", "5G_NRDUCELL"), _CSV_5G_DL,
                      period=GRANULARITY_PERIOD_MIN)
    assert value == pytest.approx(318.397, rel=1e-4)


def test_5g_throughput_ul_is_production_ready_in_the_same_unit_as_dl():
    dl = definition("throughput_dl", "5G_NRDUCELL")
    ul = definition("throughput_ul", "5G_NRDUCELL")
    assert ul.unit == dl.unit == "Mbit/s"
    assert ul.production_ready


def test_throughput_floor_accepts_the_measured_time_unit():
    value = calculate(definition("throughput_dl", "5G_NRDUCELL"), _CSV_5G_DL,
                      period=GRANULARITY_PERIOD_MIN)
    assert check_throughput_floor(definition("throughput_dl", "5G_NRDUCELL"),
                                  _CSV_5G_DL, GRANULARITY_PERIOD_MIN, value) is None


def test_throughput_floor_flags_suspect_time_unit():
    """Sob a hipótese de milissegundo o valor sai 1000× menor que o piso
    ``volume/período`` — a trava acende em vez de o número errado ser publicado."""
    dl = definition("throughput_dl", "5G_NRDUCELL")
    as_milliseconds = calculate(dl, _CSV_5G_DL, GRANULARITY_PERIOD_MIN) / 1000
    reason = check_throughput_floor(dl, _CSV_5G_DL, GRANULARITY_PERIOD_MIN, as_milliseconds)
    assert reason and "piso" in reason


def test_throughput_floor_ignores_metrics_that_are_not_throughput():
    util = definition("utilization_dl", "4G")
    assert check_throughput_floor(util, {"L.ChMeas.PRB.DL.Avail": 100.0}, 1, 0.0) is None


@pytest.mark.parametrize("metric", ["traffic_volume_dl_sa", "traffic_volume_ul_sa"])
def test_traffic_volume_sa_never_negative(metric):
    """O próprio export do OSS traz N.NSA.ThpVol maior que o total."""
    item = definition(metric, "5G_NRDUCELL")
    counters = {item.required[0]: 100.0, item.required[1]: 250.0}
    assert calculate(item, counters, period=GRANULARITY_PERIOD_MIN) == 0.0


def test_rtt_metrics_are_not_offered_by_monitoring():
    offered = {(row["id"], row["technology"]) for row in catalog_for_api()}
    assert ("ran_rtt", "4G") not in offered
    assert ("terrestrial_rtt", "4G") not in offered


@pytest.mark.parametrize("technology, metric", [
    ("4G", "throughput_dl"), ("4G", "throughput_ul"),
    ("5G_NRDUCELL", "throughput_dl"), ("5G_NRDUCELL", "throughput_ul"),
])
def test_site_throughput_recalculates_from_summed_counters(technology, metric):
    """Somar taxas de células dava 658 Mbit/s num site 4G. Taxa de site é
    Σ(volume−último)/Σtempo, e é o parser quem recalcula."""
    assert definition(metric, technology).site_aggregation == "recalculate"


def test_site_throughput_is_not_the_sum_of_cell_rates():
    dl = definition("throughput_dl", "5G_NRDUCELL")
    twin = {name: value * 2 for name, value in _CSV_5G_DL.items()}
    cell = calculate(dl, _CSV_5G_DL, GRANULARITY_PERIOD_MIN)
    site = calculate(dl, twin, GRANULARITY_PERIOD_MIN)
    assert site == pytest.approx(cell)


def test_sample_size_returns_smallest_denominator():
    access = definition("accessibility", "4G")
    counters = {"L.RRC.ConnReq.Succ": 1.0, "L.RRC.ConnReq.Att": 2.0,
                "L.E-RAB.SuccEst": 9.0, "L.E-RAB.AttEst": 9.0,
                "L.S1Sig.ConnEst.Succ": 40.0, "L.S1Sig.ConnEst.Att": 40.0}
    assert sample_size(access, counters) == 2.0
    assert sample_size(definition("utilization_dl", "4G"), counters) is None


def test_sample_size_covers_the_5g_accessibility_denominators():
    access = definition("accessibility", "5G_NRCELL")
    counters = _counters(access)
    counters["N.NGSig.ConnEst.Att"] = 3.0
    assert sample_size(access, counters) == 3.0


def test_sample_size_is_none_when_the_counters_are_absent():
    assert sample_size(definition("accessibility", "4G"), {}) is None


# ── Fase 2 / B2 — threshold com unidade ───────────────────────────────────

def test_threshold_value_accepts_legacy_raw_number():
    assert threshold_value(80, default=0) == 80


def test_threshold_value_extracts_from_new_object_format():
    assert threshold_value({"value": 80, "unit": "%"}, default=0) == 80


def test_threshold_value_falls_back_to_default_when_missing():
    assert threshold_value(None, default=95) == 95


def test_threshold_object_normalizes_legacy_raw_number():
    assert threshold_object(80, "%") == {"value": 80, "unit": "%"}


def test_threshold_object_keeps_new_format_as_is():
    assert threshold_object({"value": 80, "unit": "%"}) == {"value": 80, "unit": "%"}


def test_threshold_object_is_none_when_missing():
    assert threshold_object(None) is None
