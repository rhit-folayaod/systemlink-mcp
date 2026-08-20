"""Backend protocol and selection.

Tools never import ``nisystemlink-clients``. They call ``SystemLinkBackend``.
``SYSTEMLINK_MCP_SIMULATE=1`` forces the fake. Otherwise we construct the real
client from env config and fall back to simulation with a log line if connect
fails.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Protocol, runtime_checkable

from systemlink_mcp.config import Settings, load_settings
from systemlink_mcp.models import (
    AssetQuerySummary,
    CalibrationDueSummary,
    CalStatus,
    FailingStepsSummary,
    FileListSummary,
    GroupBy,
    MeasurementSummary,
    NotebookExecutionSummary,
    ProductQuerySummary,
    ResultQuerySummary,
    SpecQuerySummary,
    SystemQuerySummary,
    UpdateResultSummary,
    YieldSummary,
)

logger = logging.getLogger("systemlink_mcp")

_backend: SystemLinkBackend | None = None


@runtime_checkable
class SystemLinkBackend(Protocol):
    name: str
    simulated: bool

    def summarize_yield(
        self,
        *,
        part_number: str | None,
        program_name: str | None,
        serial_number: str | None,
        status: str | None,
        started_after: str | None,
        started_before: str | None,
        group_by: GroupBy,
    ) -> YieldSummary: ...

    def query_results(
        self,
        *,
        part_number: str | None,
        program_name: str | None,
        serial_number: str | None,
        status: str | None,
        started_after: str | None,
        started_before: str | None,
        preview_limit: int,
    ) -> ResultQuerySummary: ...

    def get_failing_steps(self, result_id: str, preview_limit: int) -> FailingStepsSummary: ...

    def summarize_measurement(
        self,
        *,
        table_id: str | None,
        result_id: str | None,
        preview_limit: int,
    ) -> MeasurementSummary: ...

    def list_calibration_due(
        self,
        *,
        statuses: list[CalStatus] | None,
        preview_limit: int,
    ) -> CalibrationDueSummary: ...

    def query_assets(
        self,
        *,
        name: str | None,
        serial_number: str | None,
        model_name: str | None,
        calibration_status: str | None,
        preview_limit: int,
    ) -> AssetQuerySummary: ...

    def query_systems(
        self,
        *,
        alias: str | None,
        connected: bool | None,
        preview_limit: int,
    ) -> SystemQuerySummary: ...

    def query_products(
        self,
        *,
        family: str | None,
        part_number: str | None,
        name: str | None,
        preview_limit: int,
    ) -> ProductQuerySummary: ...

    def query_specs(self, product_id: str, preview_limit: int) -> SpecQuerySummary: ...

    def list_files(
        self,
        *,
        file_ids: list[str] | None,
        result_id: str | None,
        asset_id: str | None,
        preview_limit: int,
    ) -> FileListSummary: ...

    def update_result_properties(
        self,
        result_id: str,
        *,
        keywords: list[str] | None,
        properties: dict[str, str | None] | None,
    ) -> UpdateResultSummary: ...

    def execute_notebook(
        self,
        notebook_id: str,
        workspace_id: str,
        parameters: dict[str, Any] | None,
    ) -> NotebookExecutionSummary: ...


def create_backend(settings: Settings | None = None) -> SystemLinkBackend:
    settings = settings or load_settings()
    if settings.simulate:
        from systemlink_mcp.simulate import SimulatedBackend

        logger.info("Using simulated SystemLink backend (SYSTEMLINK_MCP_SIMULATE is set).")
        return SimulatedBackend(seed=settings.seed)

    try:
        from systemlink_mcp.real import RealBackend

        backend = RealBackend(settings)
        logger.info(
            "Using real SystemLink backend at %s.",
            settings.server_uri or "(default config)",
        )
        return backend
    except Exception as exc:
        from systemlink_mcp.simulate import SimulatedBackend

        logger.warning(
            "Could not connect to SystemLink (%s: %s). Falling back to the simulated backend.",
            type(exc).__name__,
            exc,
        )
        return SimulatedBackend(seed=settings.seed)


def get_backend() -> SystemLinkBackend:
    global _backend
    if _backend is None:
        _backend = create_backend()
    return _backend


def reset_backend() -> None:
    """Test helper so write-gating and mutation tests start from a clean backend."""
    global _backend
    _backend = None


def configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
