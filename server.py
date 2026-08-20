# Copyright 2026 Timi Folayan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""MCP server that exposes NI SystemLink with test-and-measurement-native tools.

Transport: stdio by default (Cursor / Claude Desktop / Claude Code). Pass --http
for Streamable HTTP. Do not use the deprecated SSE transport.

Run: uv run server.py
Env: SYSTEMLINK_MCP_SIMULATE=1 force the fake backend
     SYSTEMLINK_MCP_ALLOW_WRITE=1 enable mutating tools (off by default)
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from typing import Any

from fastmcp import FastMCP

from systemlink_mcp.backend import configure_logging, create_backend, get_backend
from systemlink_mcp.config import load_settings
from systemlink_mcp.errors import BackendError, error_dict
from systemlink_mcp.models import CalStatus, GroupBy
from systemlink_mcp.summarize import PREVIEW_LIMIT, WAVEFORM_PREVIEW

mcp = FastMCP("systemlink-mcp")
configure_logging()

MAX_PREVIEW = 50
MAX_WAVEFORM_PREVIEW = 100


def _cap(limit: int | None, default: int, ceiling: int) -> int:
    value = default if limit is None else limit
    if value < 0:
        return 0
    return min(value, ceiling)


def _writes_enabled() -> bool:
    return load_settings().allow_write


def _refuse_write() -> dict[str, Any]:
    return error_dict(
        "Write tools are disabled. Set SYSTEMLINK_MCP_ALLOW_WRITE=1 to enable mutating operations.",
        code="writes_disabled",
    )


def _run(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        return result.model_dump() if hasattr(result, "model_dump") else result
    except BackendError as exc:
        return exc.to_dict()


@mcp.tool
def summarize_yield(
    part_number: str | None = None,
    program_name: str | None = None,
    serial_number: str | None = None,
    status: str | None = None,
    started_after: str | None = None,
    started_before: str | None = None,
    group_by: GroupBy = "none",
) -> dict[str, Any]:
    """Summarize pass/fail yield for test results.

    Uses Test Monitor result-level status, not CMMS work-order counts. Filters
    are pushed to the server as Dynamic LINQ (partNumber, programName,
    serialNumber, status.statusType, startedAt). Yield is passed / (passed + failed).

    part_number: Product revision / part number, e.g. PN-5164-B.
    program_name: Test program or sequence name, e.g. BoardFunctional.seq.
    serial_number: DUT serial.
    status: Optional status.statusType filter (PASSED, FAILED, ERRORED, ...).
    started_after: Inclusive ISO-8601 UTC start, e.g. 2026-01-01T00:00:00Z.
    started_before: Inclusive ISO-8601 UTC end.
    group_by: Break yield down by part_number, program_name, serial_number, or none.
    """
    return _run(
        get_backend().summarize_yield,
        part_number=part_number,
        program_name=program_name,
        serial_number=serial_number,
        status=status,
        started_after=started_after,
        started_before=started_before,
        group_by=group_by,
    )


@mcp.tool
def query_results(
    part_number: str | None = None,
    program_name: str | None = None,
    serial_number: str | None = None,
    status: str | None = None,
    started_after: str | None = None,
    started_before: str | None = None,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """Query test results and return counts plus a bounded recent preview.

    Does not dump the full result set. Counts use Test Monitor return_count;
    the preview is the newest matching results only.

    part_number: Product revision / part number.
    program_name: Test program or sequence name.
    serial_number: DUT serial.
    status: Optional status.statusType filter (PASSED, FAILED, ERRORED, ...).
    started_after: Inclusive ISO-8601 UTC start.
    started_before: Inclusive ISO-8601 UTC end.
    preview_limit: Max result rows to include (capped at 50).
    """
    return _run(
        get_backend().query_results,
        part_number=part_number,
        program_name=program_name,
        serial_number=serial_number,
        status=status,
        started_after=started_after,
        started_before=started_before,
        preview_limit=_cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def get_failing_steps(result_id: str, preview_limit: int = PREVIEW_LIMIT) -> dict[str, Any]:
    """Return failing steps for one test result, with measurements versus limits.

    This is the DUT-debug view: which step failed and how the measurement
    compared to the spec window. Step payloads are summarized; only failing
    steps are previewed.

    result_id: Test Monitor result id.
    preview_limit: Max failing steps to include (capped at 50).
    """
    return _run(
        get_backend().get_failing_steps,
        result_id,
        _cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def summarize_measurement(
    table_id: str | None = None,
    result_id: str | None = None,
    preview_limit: int = WAVEFORM_PREVIEW,
) -> dict[str, Any]:
    """Summarize a measurement table: stats plus a downsampled trace preview.

    On a live server this uses DataFrameClient.query_decimated_data rather than
    downloading every row. Pass table_id directly, or result_id to use the first
    attached data table.

    table_id: DataFrame table id.
    result_id: Test result whose data_table_ids should be used if table_id is omitted.
    preview_limit: Max preview points (capped at 100).
    """
    return _run(
        get_backend().summarize_measurement,
        table_id=table_id,
        result_id=result_id,
        preview_limit=_cap(preview_limit, WAVEFORM_PREVIEW, MAX_WAVEFORM_PREVIEW),
    )


@mcp.tool
def list_calibration_due(
    include_out_for_calibration: bool = False,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """List instruments whose calibration is overdue or approaching due.

    This is the PXI-fleet question a generic CMMS wrapper cannot ask: which
    chassis or module needs cal, and when. Default statuses are
    PAST_RECOMMENDED_DUE_DATE and APPROACHING_RECOMMENDED_DUE_DATE.

    include_out_for_calibration: Also include OUT_FOR_CALIBRATION assets.
    preview_limit: Max assets to include (capped at 50).
    """
    statuses: list[CalStatus] = [
        "APPROACHING_RECOMMENDED_DUE_DATE",
        "PAST_RECOMMENDED_DUE_DATE",
    ]
    if include_out_for_calibration:
        statuses.append("OUT_FOR_CALIBRATION")
    return _run(
        get_backend().list_calibration_due,
        statuses=statuses,
        preview_limit=_cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def query_assets(
    name: str | None = None,
    serial_number: str | None = None,
    model_name: str | None = None,
    calibration_status: str | None = None,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """Query the asset inventory: model, serial, presence, and calibration state.

    name: Substring match on asset name.
    serial_number: Exact serial.
    model_name: Substring match on model, e.g. PXIe-6368.
    calibration_status: OK, APPROACHING_RECOMMENDED_DUE_DATE,
        PAST_RECOMMENDED_DUE_DATE, or OUT_FOR_CALIBRATION.
    preview_limit: Max assets to include (capped at 50).
    """
    return _run(
        get_backend().query_assets,
        name=name,
        serial_number=serial_number,
        model_name=model_name,
        calibration_status=calibration_status,
        preview_limit=_cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def query_systems(
    alias: str | None = None,
    connected: bool | None = None,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """Query registered test systems and their connection health.

    alias: System alias or id substring.
    connected: If set, filter on status.data.http_connected.
    preview_limit: Max systems to include (capped at 50).
    """
    return _run(
        get_backend().query_systems,
        alias=alias,
        connected=connected,
        preview_limit=_cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def query_products(
    family: str | None = None,
    part_number: str | None = None,
    name: str | None = None,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """Query products and part numbers used to correlate test results by revision.

    family: Product family, e.g. Oscilloscopes.
    part_number: Exact part number / revision.
    name: Exact product name.
    preview_limit: Max products to include (capped at 50).
    """
    return _run(
        get_backend().query_products,
        family=family,
        part_number=part_number,
        name=name,
        preview_limit=_cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def query_specs(product_id: str, preview_limit: int = PREVIEW_LIMIT) -> dict[str, Any]:
    """Query specification limits for a product (min / typical / max, unit).

    product_id: Product id or, in simulation, the part number used as product_id.
    preview_limit: Max specs to include (capped at 50).
    """
    return _run(
        get_backend().query_specs,
        product_id,
        _cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def list_files(
    file_ids: list[str] | None = None,
    result_id: str | None = None,
    asset_id: str | None = None,
    preview_limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """List file metadata attached to a result or asset. Does not download contents.

    file_ids: Explicit file ids.
    result_id: Test result whose file_ids should be listed.
    asset_id: Asset whose linked files should be listed.
    preview_limit: Max files to include (capped at 50).
    """
    return _run(
        get_backend().list_files,
        file_ids=file_ids,
        result_id=result_id,
        asset_id=asset_id,
        preview_limit=_cap(preview_limit, PREVIEW_LIMIT, MAX_PREVIEW),
    )


@mcp.tool
def update_result_properties(
    result_id: str,
    keywords: list[str] | None = None,
    properties: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Update keywords or properties on a test result. Requires SYSTEMLINK_MCP_ALLOW_WRITE=1.

    Uses TestMonitorClient.update_result. Asset metadata cannot be patched
    through the current AssetManagementClient, which has no update method.

    result_id: Test Monitor result id.
    keywords: Replacement keyword list if provided.
    properties: Properties to merge onto the result.
    """
    if not _writes_enabled():
        return _refuse_write()
    return _run(
        get_backend().update_result_properties,
        result_id,
        keywords=keywords,
        properties=properties,
    )


@mcp.tool
def execute_notebook(
    notebook_id: str,
    workspace_id: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a SystemLink Jupyter notebook execution. Requires SYSTEMLINK_MCP_ALLOW_WRITE=1.

    Uses NotebookClient.create_executions. This queues work on the server; it
    does not wait for completion or return notebook output.

    notebook_id: Notebook id to run.
    workspace_id: Workspace the execution belongs to.
    parameters: Optional notebook parameters (values are stringified for the SDK).
    """
    if not _writes_enabled():
        return _refuse_write()
    return _run(
        get_backend().execute_notebook,
        notebook_id,
        workspace_id,
        parameters,
    )


def _probe() -> int:
    """Connect to SystemLink and print a small live sample. Never falls back to sim."""
    configure_logging()
    settings = replace(load_settings(), require_real=True)
    try:
        backend = create_backend(settings)
    except BackendError as exc:
        print(json.dumps(exc.to_dict(), indent=2), flush=True)
        return 1
    payload = _run(
        backend.query_systems,
        alias=None,
        connected=None,
        preview_limit=5,
    )
    report = {
        "ok": payload.get("ok", True),
        "backend": getattr(backend, "name", None),
        "simulated": getattr(backend, "simulated", None),
        "query_systems": payload,
    }
    print(json.dumps(report, indent=2, default=str), flush=True)
    if payload.get("ok") is False or getattr(backend, "simulated", True):
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="systemlink-mcp MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP instead of stdio (endpoint /mcp).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Connect to live SystemLink, print a sample, and exit. Does not start MCP.",
    )
    args = parser.parse_args(argv)
    if args.probe:
        raise SystemExit(_probe())
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
