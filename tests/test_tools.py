from __future__ import annotations

import server
from systemlink_mcp.models import (
    AssetQuerySummary,
    CalibrationDueSummary,
    FailingStepsSummary,
    FileListSummary,
    MeasurementSummary,
    ProductQuerySummary,
    ResultQuerySummary,
    SpecQuerySummary,
    SystemQuerySummary,
    YieldSummary,
)
from systemlink_mcp.summarize import PREVIEW_LIMIT


def test_every_read_tool_returns_schema_valid_payload() -> None:
    yield_payload = server.summarize_yield(group_by="part_number")
    YieldSummary.model_validate(yield_payload)
    assert yield_payload["ok"] is True
    assert yield_payload["simulated"] is True
    assert yield_payload["overall"]["total"] > 0

    results = server.query_results(preview_limit=PREVIEW_LIMIT)
    ResultQuerySummary.model_validate(results)
    assert results["matched"] >= len(results["preview"])

    failing = server.get_failing_steps("res-fail-dut1001")
    FailingStepsSummary.model_validate(failing)
    assert failing["failing_step_count"] >= 1
    assert failing["failing_steps"][0]["measurements"]

    measurement = server.summarize_measurement(table_id="tbl-gain-trace")
    MeasurementSummary.model_validate(measurement)
    assert measurement["row_count"] == 2000

    cal = server.list_calibration_due()
    CalibrationDueSummary.model_validate(cal)
    assert cal["matched"] >= 2

    assets = server.query_assets(model_name="PXIe-6368")
    AssetQuerySummary.model_validate(assets)
    assert assets["matched"] == 1

    systems = server.query_systems()
    SystemQuerySummary.model_validate(systems)
    assert systems["matched"] == 3

    products = server.query_products(family="Oscilloscopes")
    ProductQuerySummary.model_validate(products)
    assert products["matched"] == 2

    specs = server.query_specs("PN-5164-B")
    SpecQuerySummary.model_validate(specs)
    assert specs["matched"] == 2

    files = server.list_files(result_id="res-fail-dut1001")
    FileListSummary.model_validate(files)
    assert files["matched"] == 1
    assert files["preview"][0]["name"].endswith(".html")
