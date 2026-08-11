import math

import pytest

from core.kpi_formulas import CATALOG, InvalidKpi, calculate


def _counters(definition):
    # Vetor conhecido não nulo para cada contador. Mantém denominadores positivos
    # inclusive nas fórmulas 5G com subtrações.
    return {name: 10.0 for name in definition.required}


@pytest.mark.parametrize("definition", CATALOG, ids=lambda item: f"{item.technology}-{item.id}")
def test_every_catalog_formula_has_a_deterministic_numeric_vector(definition):
    value = calculate(definition, _counters(definition), period=5)
    assert isinstance(value, float)
    assert math.isfinite(value)


@pytest.mark.parametrize("definition", [item for item in CATALOG if len(item.required) > 1])
def test_missing_required_counter_is_invalid(definition):
    counters = _counters(definition)
    counters.pop(definition.required[0])
    with pytest.raises(InvalidKpi, match="contador ausente"):
        calculate(definition, counters, period=5)


@pytest.mark.parametrize("definition", [item for item in CATALOG if "availability" == item.id])
def test_availability_requires_the_period_returned_by_oss(definition):
    with pytest.raises(InvalidKpi, match="period"):
        calculate(definition, _counters(definition), period=None)
