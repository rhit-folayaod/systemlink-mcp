"""Pydantic models for tool responses and backend DTOs.

These types are independent of ``nisystemlink-clients``. The real backend maps
SDK objects onto them; the simulator constructs them directly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GroupBy = Literal["none", "part_number", "program_name", "serial_number"]
CalStatus = Literal[
    "OK",
    "APPROACHING_RECOMMENDED_DUE_DATE",
    "PAST_RECOMMENDED_DUE_DATE",
    "OUT_FOR_CALIBRATION",
]


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: str
    code: str = "error"
    http_status: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ResultPreview(BaseModel):
    id: str
    part_number: str | None = None
    serial_number: str | None = None
    program_name: str | None = None
    status: str | None = None
    host_name: str | None = None
    started_at: str | None = None
    total_time_in_seconds: float | None = None
    file_ids: list[str] = Field(default_factory=list)
    data_table_ids: list[str] = Field(default_factory=list)


class StatusCounts(BaseModel):
    passed: int = 0
    failed: int = 0
    errored: int = 0
    running: int = 0
    other: int = 0
    total: int = 0


class YieldGroup(BaseModel):
    key: str
    counts: StatusCounts
    yield_percent: float | None = None


class YieldSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    filters: dict[str, Any]
    group_by: GroupBy
    overall: StatusCounts
    yield_percent: float | None = None
    groups: list[YieldGroup] = Field(default_factory=list)
    note: str = (
        "Yield is passed / (passed + failed) at the result level. "
        "Other statuses are counted but excluded from the yield ratio."
    )


class ResultQuerySummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    filters: dict[str, Any]
    counts: StatusCounts
    yield_percent: float | None = None
    matched: int
    preview_limit: int
    preview: list[ResultPreview]
    truncated: bool


class MeasurementVsLimit(BaseModel):
    name: str | None = None
    status: str | None = None
    measurement: str | None = None
    low_limit: str | None = None
    high_limit: str | None = None
    units: str | None = None


class FailingStep(BaseModel):
    step_id: str | None = None
    name: str | None = None
    path: str | None = None
    status: str | None = None
    measurements: list[MeasurementVsLimit] = Field(default_factory=list)


class FailingStepsSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    result: ResultPreview
    failing_step_count: int
    total_step_count: int
    preview_limit: int
    failing_steps: list[FailingStep]
    truncated: bool


class ColumnInfo(BaseModel):
    name: str
    data_type: str | None = None
    column_type: str | None = None


class MeasurementSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    table_id: str
    table_name: str | None = None
    row_count: int
    columns: list[ColumnInfo] = Field(default_factory=list)
    numeric_column: str | None = None
    stats: dict[str, float | int | None] = Field(default_factory=dict)
    preview_limit: int
    preview: list[float] = Field(default_factory=list)
    truncated: bool
    note: str = (
        "Preview is a downsampled trace, not the raw table. "
        "On a live server this uses DataFrameClient.query_decimated_data."
    )


class AssetPreview(BaseModel):
    id: str | None = None
    name: str | None = None
    model_name: str | None = None
    serial_number: str | None = None
    part_number: str | None = None
    asset_type: str | None = None
    calibration_status: str | None = None
    cal_due: str | None = None
    location: str | None = None
    presence: str | None = None
    keywords: list[str] = Field(default_factory=list)


class CalibrationDueSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    matched: int
    by_status: dict[str, int] = Field(default_factory=dict)
    preview_limit: int
    preview: list[AssetPreview]
    truncated: bool


class AssetQuerySummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    filters: dict[str, Any]
    matched: int
    preview_limit: int
    preview: list[AssetPreview]
    truncated: bool


class SystemPreview(BaseModel):
    id: str | None = None
    alias: str | None = None
    connected: bool | None = None
    os: str | None = None
    status: str | None = None


class SystemQuerySummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    filters: dict[str, Any]
    matched: int
    connected_count: int
    preview_limit: int
    preview: list[SystemPreview]
    truncated: bool


class ProductPreview(BaseModel):
    id: str | None = None
    part_number: str | None = None
    name: str | None = None
    family: str | None = None


class ProductQuerySummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    filters: dict[str, Any]
    matched: int
    preview_limit: int
    preview: list[ProductPreview]
    truncated: bool


class SpecPreview(BaseModel):
    id: str | None = None
    spec_id: str | None = None
    product_id: str | None = None
    name: str | None = None
    category: str | None = None
    spec_type: str | None = None
    unit: str | None = None
    limit_min: float | None = None
    limit_typical: float | None = None
    limit_max: float | None = None


class SpecQuerySummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    product_id: str
    matched: int
    preview_limit: int
    preview: list[SpecPreview]
    truncated: bool


class FilePreview(BaseModel):
    id: str | None = None
    name: str | None = None
    size: int | None = None
    created: str | None = None


class FileListSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    matched: int
    preview_limit: int
    preview: list[FilePreview]
    truncated: bool
    note: str = "Metadata only. File contents are not downloaded into the model context."


class UpdateResultSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    result_id: str
    keywords: list[str] = Field(default_factory=list)
    properties: dict[str, str | None] = Field(default_factory=dict)


class NotebookExecutionSummary(BaseModel):
    ok: Literal[True] = True
    backend: str
    simulated: bool
    execution_id: str | None = None
    notebook_id: str
    workspace_id: str
    status: str | None = None
    cached_result: bool | None = None
