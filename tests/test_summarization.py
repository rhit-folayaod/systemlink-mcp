from __future__ import annotations

import server
from systemlink_mcp.summarize import WAVEFORM_PREVIEW


def test_measurement_preview_is_bounded_for_large_table() -> None:
    payload = server.summarize_measurement(
        table_id="tbl-gain-trace",
        preview_limit=WAVEFORM_PREVIEW,
    )
    assert payload["row_count"] == 2000
    assert len(payload["preview"]) <= WAVEFORM_PREVIEW
    assert payload["truncated"] is True
    assert payload["stats"]["count"] == 2000
    assert payload["stats"]["max"] == 2.47


def test_result_preview_never_exceeds_limit() -> None:
    payload = server.query_results(preview_limit=5)
    assert payload["matched"] > 5
    assert len(payload["preview"]) == 5
    assert payload["truncated"] is True


def test_query_results_cap_from_tool_ceiling() -> None:
    payload = server.query_results(preview_limit=10_000)
    assert len(payload["preview"]) <= 50
