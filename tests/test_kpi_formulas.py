import math

import pytest

from core.kpi_formulas import (
    CATALOG, InvalidKpi, KpiDefinition, NotApplicable, calculate, catalog_for_api,
    check_throughput_floor, definition, sample_size, to_canonical,
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

# ── Fase 3 / B1 — unidade canônica na leitura ──────────────────────────────────

def test_every_definition_declares_base_unit():
    """Sem base declarada, o frontend recebe unidade implícita — o defeito B1."""
    faltando = [(item.id, item.technology) for item in CATALOG if not item.base_unit]
    assert faltando == []


def test_to_canonical_converts_5g_kbit_to_bit():
    assert to_canonical("traffic_volume_dl_sa", "5G_NRDUCELL", 1.0) == 1000.0


def test_to_canonical_converts_throughput_mbit_per_second_to_bit_per_second():
    assert to_canonical("throughput_dl", "4G", 6.776) == pytest.approx(6_776_000.0)
    assert to_canonical("throughput_ul", "5G_NRDUCELL", 1.0) == pytest.approx(1e6)


def test_to_canonical_leaves_unknown_technology_untouched():
    """Banco anterior à coluna ``technology`` (volume em MB) fica fora do contrato.

    O par ``(metric, technology)`` é o que identifica a base gravada; sem ele
    não há como escolher fator, e inventar um seria pior que não converter.
    """
    assert to_canonical("traffic_volume_dl", None, 123.0) == 123.0
    assert to_canonical("traffic_volume_dl", "", 123.0) == 123.0


def test_to_canonical_leaves_metric_outside_the_catalog_untouched():
    assert to_canonical("dl_prb_usage", "4G", 42.0) == 42.0


def test_percentage_and_dbm_metrics_are_not_rescaled():
    assert to_canonical("utilization_dl", "4G", 80.0) == 80.0
    assert to_canonical("availability", "5G_NRCELL", 100.0) == 100.0
    assert to_canonical("interference_ul", "5G_NRDUCELL", -110.0) == -110.0
    assert to_canonical("user_count", "5G_NRCELL", 19.356) == 19.356


def test_4g_and_5g_volume_share_the_same_base_unit():
    """4G grava bit e 5G grava kbit sob nomes de métrica diferentes; a base é uma só."""
    quatro_g = definition("traffic_volume_dl", "4G")
    cinco_g = definition("traffic_volume_dl_nsa", "5G_NRDUCELL")
    assert quatro_g.base_unit == cinco_g.base_unit == "bit"
    assert (quatro_g.to_base, cinco_g.to_base) == (1.0, 1e3)


def test_paired_throughput_metrics_share_the_same_base_unit():
    for technology in ("4G", "5G_NRDUCELL"):
        dl = definition("throughput_dl", technology)
        ul = definition("throughput_ul", technology)
        assert dl.base_unit == ul.base_unit == "bit/s"


def test_catalog_announces_the_canonical_unit_and_keeps_the_oss_one():
    rows = {(item["id"], item["technology"]): item for item in catalog_for_api()}
    volume = rows[("traffic_volume_dl_sa", "5G_NRDUCELL")]
    assert (volume["unit"], volume["oss_unit"]) == ("bit", "kbit")
    throughput = rows[("throughput_dl", "4G")]
    assert (throughput["unit"], throughput["oss_unit"]) == ("bit/s", "Mbit/s")


def test_legacy_traffic_volume_keeps_monitoring_available():
    """B8: removê-las do seletor mataria a coluna "Participação" da lista de sites."""
    oferecidos = {(item["id"], item["technology"]) for item in catalog_for_api()}
    assert ("traffic_volume_dl", "4G") in oferecidos
    assert ("traffic_volume_ul", "4G") in oferecidos


def test_legacy_traffic_volume_is_named_by_the_data_not_by_the_code():
    """B8: "(legado)" descrevia a origem do código e "contador OSS" não é unidade."""
    for metric in ("traffic_volume_dl", "traffic_volume_ul"):
        item = definition(metric, "4G")
        assert "legado" not in item.name
        assert item.unit == "bit"


def test_definition_without_a_known_unit_fails_at_import_time():
    """Unidade nova sem base derruba o import — nunca vira conversão por suposição."""
    with pytest.raises(ValueError, match="sem base"):
        KpiDefinition("x", "4G", "X", "Gbit", ("C",), "sum", lambda c, p: 0.0)
