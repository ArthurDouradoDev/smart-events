import pytest

from core.collection_result import CollectionResult


def test_data_requires_measurement():
    with pytest.raises(ValueError):
        CollectionResult("data")


@pytest.mark.parametrize("state", ["empty", "partial", "error", "auth_required"])
def test_explicit_non_data_states_are_preserved(state):
    result = CollectionResult(state, cause="motivo")
    assert result.state == state
    assert result.cause == "motivo"


def test_error_includes_display_safe_diagnostic():
    result = CollectionResult.error("HTTP 500", code="http")
    assert result.diagnostics[0].as_dict()["stage"] == "request"
    assert result.as_status_fields()["cause"] == "HTTP 500"
