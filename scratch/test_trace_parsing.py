import os
import sys
from unittest.mock import MagicMock

# Add root folder to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.collector import HttpCollector

def test_parse_filtered_trace_response():
    # Mock event configuration
    mock_event = {
        "id": "test-event",
        "name": "Test Event",
        "oss": {"base_url": "http://mock-oss.com"},
        "thresholds": {}
    }
    
    # Initialize collector
    collector = HttpCollector(mock_event, "http://mock-oss.com")
    
    # Mock internal methods that call external services or parse inner payloads
    collector._fetch_msg_explain_info = MagicMock(return_value={"mock_decoded_field": True})
    collector._extract_rsrp_rsrq_from_json = MagicMock(return_value=(-95.0, -12.0))
    collector._cell_in_event = MagicMock(return_value=True)
    
    # Mock trace response data (filter-by-cols structure)
    mock_response = {
        "data": {
            "tableData": [
                {
                    "serialNo": 10564,
                    "source": "SR-RPITJ2",
                    "payload": [
                        {"name": "GLCellId", "value": "2"},
                        {"name": "Time", "value": "2026-05-29 11:00:00"}
                    ]
                },
                {
                    "serialNo": 10565,
                    "source": "SR-RPITJ3",
                    "payload": [
                        {"name": "GLCellId", "value": "3"},
                        {"name": "Time", "value": "2026-05-29 11:01:00"}
                    ]
                }
            ]
        }
    }
    
    # Call target method
    results = collector._parse_filtered_trace_response(
        response_json=mock_response,
        task_id=123,
        session=MagicMock(),
        vip_name="John Doe",
        sess_msg_id=1
    )
    
    # Assert result length and values
    print(f"Parsed results: {results}")
    assert len(results) == 2, f"Expected 2 rows, got {len(results)}"
    
    # Check first row
    r1 = results[0]
    assert r1["vip_name"] == "John Doe"
    assert r1["rsrp"] == -95.0
    assert r1["rsrq"] == -12.0
    assert r1["serving_cell"] == "SR-RPITJ2_2"
    
    # Verify _fetch_msg_explain_info was called with 1-based indices (1 and 2), NOT serialNo (10564, 10565)
    calls = collector._fetch_msg_explain_info.call_args_list
    assert len(calls) == 2
    # call_args is (args, kwargs). args[3] is row_no.
    row_no_1 = calls[0][0][3]
    row_no_2 = calls[1][0][3]
    
    print(f"Row no for call 1: {row_no_1} (expected: 1)")
    print(f"Row no for call 2: {row_no_2} (expected: 2)")
    
    assert row_no_1 == 1, f"Expected row_no 1, got {row_no_1}"
    assert row_no_2 == 2, f"Expected row_no 2, got {row_no_2}"
    
    print("SUCCESS: _parse_filtered_trace_response unit test verified successfully!")

if __name__ == "__main__":
    test_parse_filtered_trace_response()
