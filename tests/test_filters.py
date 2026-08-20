from __future__ import annotations

import server


def test_status_filter_returns_only_failed_results() -> None:
    payload = server.query_results(status="FAILED")
    assert payload["counts"]["failed"] == payload["matched"]
    assert payload["counts"]["passed"] == 0
    assert all(item["status"] == "FAILED" for item in payload["preview"])


def test_part_number_filter_narrows_results() -> None:
    all_results = server.query_results()
    filtered = server.query_results(part_number="PN-5164-B")
    assert filtered["matched"] < all_results["matched"]
    assert all(item["part_number"] == "PN-5164-B" for item in filtered["preview"])


def test_date_filter_excludes_older_results() -> None:
    recent = server.query_results(started_after="2026-01-01T00:00:00Z")
    older = server.query_results(started_before="2025-06-01T00:00:00Z")
    assert recent["matched"] > 0
    assert older["matched"] > 0
    for item in recent["preview"]:
        assert item["started_at"] >= "2026-01-01T00:00:00Z"
    for item in older["preview"]:
        assert item["started_at"] <= "2025-06-01T00:00:00Z"
    assert recent["matched"] + older["matched"] <= server.query_results()["matched"]


def test_serial_filter_and_failing_dut() -> None:
    payload = server.query_results(serial_number="DUT-1001", status="FAILED")
    assert payload["matched"] >= 1
    failing = server.get_failing_steps("res-fail-dut1001")
    gain = failing["failing_steps"][0]["measurements"][0]
    assert gain["name"] == "Gain"
    assert float(gain["measurement"]) > float(gain["high_limit"])
