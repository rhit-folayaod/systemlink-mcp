"""Live SystemLink backend. ``nisystemlink-clients`` is imported only in this module."""

from __future__ import annotations

from typing import Any

from systemlink_mcp.config import Settings
from systemlink_mcp.errors import BackendError
from systemlink_mcp.models import (
    AssetPreview,
    AssetQuerySummary,
    CalibrationDueSummary,
    CalStatus,
    ColumnInfo,
    FailingStep,
    FailingStepsSummary,
    FileListSummary,
    FilePreview,
    GroupBy,
    MeasurementSummary,
    MeasurementVsLimit,
    NotebookExecutionSummary,
    ProductPreview,
    ProductQuerySummary,
    ResultPreview,
    ResultQuerySummary,
    SpecPreview,
    SpecQuerySummary,
    StatusCounts,
    SystemPreview,
    SystemQuerySummary,
    UpdateResultSummary,
    YieldGroup,
    YieldSummary,
)
from systemlink_mcp.pagination import walk_continuation, walk_skip_take
from systemlink_mcp.summarize import bounded_preview, downsample, numeric_stats

_STATUS_TYPES = ("PASSED", "FAILED", "ERRORED", "RUNNING")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _clause(field_name: str, value: str | None, *, contains: bool = False) -> str | None:
    if not value:
        return None
    escaped = _escape(value)
    if contains:
        return f'{field_name}.Contains("{escaped}")'
    return f'{field_name} == "{escaped}"'


def _and(clauses: list[str | None]) -> str | None:
    parts = [c for c in clauses if c]
    if not parts:
        return None
    return " && ".join(f"({p})" for p in parts)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        text = value.isoformat()
        return text.replace("+00:00", "Z")
    return str(value)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _yield_percent(counts: StatusCounts) -> float | None:
    denom = counts.passed + counts.failed
    if denom == 0:
        return None
    return round(100.0 * counts.passed / denom, 2)


def _wrap_api(exc: Exception) -> BackendError:
    http_status = getattr(exc, "http_status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    error = getattr(exc, "error", None)
    details: dict[str, Any] = {}
    if error is not None:
        details["name"] = getattr(error, "name", None)
        details["api_message"] = getattr(error, "message", None)
    return BackendError(
        message,
        code="systemlink_api",
        http_status=http_status,
        details={k: v for k, v in details.items() if v is not None},
    )


class RealBackend:
    name = "systemlink"
    simulated = False

    def __init__(self, settings: Settings) -> None:
        # Imported here so the simulated path never loads the SDK.
        from nisystemlink.clients.assetmanagement import AssetManagementClient
        from nisystemlink.clients.core import (
            ApiException,
            HttpConfiguration,
            HttpConfigurationManager,
        )
        from nisystemlink.clients.dataframe import DataFrameClient
        from nisystemlink.clients.file import FileClient
        from nisystemlink.clients.notebook import NotebookClient
        from nisystemlink.clients.product import ProductClient
        from nisystemlink.clients.spec import SpecClient
        from nisystemlink.clients.systems import SystemsClient
        from nisystemlink.clients.testmonitor import TestMonitorClient

        self._ApiException = ApiException
        try:
            if settings.server_uri:
                kwargs: dict[str, Any] = {
                    "server_uri": settings.server_uri,
                    "verify": settings.verify_tls,
                }
                if settings.workspace:
                    kwargs["workspace"] = settings.workspace
                if settings.api_key:
                    kwargs["api_key"] = settings.api_key
                elif settings.username and settings.password:
                    kwargs["username"] = settings.username
                    kwargs["password"] = settings.password
                configuration = HttpConfiguration(**kwargs)
            elif settings.api_key:
                from nisystemlink.clients.core import CloudHttpConfiguration

                configuration = CloudHttpConfiguration(settings.api_key)
            else:
                configuration = HttpConfigurationManager.get_configuration()
            self._test = TestMonitorClient(configuration=configuration)
            self._product = ProductClient(configuration=configuration)
            self._dataframe = DataFrameClient(configuration=configuration)
            self._assets = AssetManagementClient(configuration=configuration)
            self._systems = SystemsClient(configuration=configuration)
            self._spec = SpecClient(configuration=configuration)
            self._files = FileClient(configuration=configuration)
            self._notebook = NotebookClient(configuration=configuration)
            self._test.api_info()
        except ApiException as exc:
            raise _wrap_api(exc) from exc
        except Exception as exc:
            raise BackendError(
                f"Could not construct SystemLink clients: {exc}",
                code="connect_failed",
            ) from exc

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except self._ApiException as exc:
            raise _wrap_api(exc) from exc

    def _result_filter(
        self,
        *,
        part_number: str | None,
        program_name: str | None,
        serial_number: str | None,
        status: str | None,
        started_after: str | None,
        started_before: str | None,
    ) -> str | None:
        clauses = [
            _clause("partNumber", part_number),
            _clause("programName", program_name),
            _clause("serialNumber", serial_number),
            _clause("status.statusType", status.upper() if status else None),
        ]
        if started_after:
            clauses.append(f'startedAt >= "{_escape(started_after)}"')
        if started_before:
            clauses.append(f'startedAt <= "{_escape(started_before)}"')
        return _and(clauses)

    def _count_results(self, filter_str: str | None) -> int:
        from nisystemlink.clients.testmonitor.models import QueryResultsRequest

        response = self._call(
            self._test.query_results,
            QueryResultsRequest(filter=filter_str, take=1, return_count=True),
        )
        return int(getattr(response, "total_count", None) or getattr(response, "count", 0) or 0)

    def _status_counts(self, base_filter: str | None) -> StatusCounts:
        counts = StatusCounts()
        named = 0
        for status in _STATUS_TYPES:
            clause = _clause("status.statusType", status)
            combined = _and([base_filter, clause])
            value = self._count_results(combined)
            setattr(counts, status.lower(), value)
            named += value
        counts.total = self._count_results(base_filter)
        counts.other = max(counts.total - named, 0)
        return counts

    def _preview_from_sdk(self, result: Any) -> ResultPreview:
        status = getattr(result, "status", None)
        status_type = None
        if status is not None:
            status_type = _enum_value(getattr(status, "status_type", None))
        return ResultPreview(
            id=str(getattr(result, "id", "") or ""),
            part_number=getattr(result, "part_number", None),
            serial_number=getattr(result, "serial_number", None),
            program_name=getattr(result, "program_name", None),
            status=status_type,
            host_name=getattr(result, "host_name", None),
            started_at=_iso(getattr(result, "started_at", None)),
            total_time_in_seconds=getattr(result, "total_time_in_seconds", None),
            file_ids=[fid for fid in (getattr(result, "file_ids", None) or []) if fid],
            data_table_ids=[tid for tid in (getattr(result, "data_table_ids", None) or []) if tid],
        )

    def _asset_preview(self, asset: Any) -> AssetPreview:
        location = getattr(asset, "location", None)
        presence = None
        location_name = None
        if location is not None:
            location_name = getattr(location, "minion_id", None) or getattr(
                location, "system_id", None
            )
            state = getattr(location, "state", None)
            if state is not None:
                presence = _enum_value(getattr(state, "asset_presence", state))
        cal = getattr(asset, "external_calibration", None)
        cal_due = getattr(cal, "resolved_due_date", None) if cal is not None else None
        if cal_due is None and cal is not None:
            cal_due = getattr(cal, "next_custom_due_date", None) or getattr(
                cal, "next_recommended_date", None
            )
        return AssetPreview(
            id=getattr(asset, "id", None),
            name=getattr(asset, "name", None),
            model_name=getattr(asset, "model_name", None),
            serial_number=getattr(asset, "serial_number", None),
            part_number=getattr(asset, "part_number", None),
            asset_type=_enum_value(getattr(asset, "asset_type", None)),
            calibration_status=_enum_value(getattr(asset, "calibration_status", None)),
            cal_due=_iso(cal_due),
            location=location_name,
            presence=presence,
            keywords=list(getattr(asset, "keywords", None) or []),
        )

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
    ) -> YieldSummary:
        from nisystemlink.clients.testmonitor.models import (
            QueryResultsRequest,
            ResultOrderByField,
            ResultProjection,
        )

        base = self._result_filter(
            part_number=part_number,
            program_name=program_name,
            serial_number=serial_number,
            status=status,
            started_after=started_after,
            started_before=started_before,
        )
        overall = self._status_counts(base)
        groups: list[YieldGroup] = []
        if group_by != "none":
            projection_map = {
                "part_number": ResultProjection.PART_NUMBER,
                "program_name": ResultProjection.PROGRAM_NAME,
                "serial_number": ResultProjection.SERIAL_NUMBER,
            }
            attr_map = {
                "part_number": "part_number",
                "program_name": "program_name",
                "serial_number": "serial_number",
            }
            rows = walk_continuation(
                lambda **kw: self._call(
                    self._test.query_results,
                    QueryResultsRequest(
                        filter=base,
                        projection=[projection_map[group_by], ResultProjection.STATUS],
                        order_by=ResultOrderByField.STARTED_AT,
                        descending=True,
                        return_count=False,
                        take=kw.get("take"),
                        continuation_token=kw.get("continuation_token"),
                    ),
                ),
                "results",
            )
            buckets: dict[str, list[str]] = {}
            for row in rows:
                key = getattr(row, attr_map[group_by], None) or "(none)"
                row_status = getattr(getattr(row, "status", None), "status_type", None)
                buckets.setdefault(str(key), []).append(_enum_value(row_status) or "OTHER")
            for key, statuses in sorted(buckets.items()):
                counts = StatusCounts(total=len(statuses))
                for item in statuses:
                    lower = item.lower()
                    if lower in {"passed", "failed", "errored", "running"}:
                        setattr(counts, lower, getattr(counts, lower) + 1)
                    else:
                        counts.other += 1
                groups.append(
                    YieldGroup(key=key, counts=counts, yield_percent=_yield_percent(counts))
                )
        return YieldSummary(
            backend=self.name,
            simulated=False,
            filters={
                "part_number": part_number,
                "program_name": program_name,
                "serial_number": serial_number,
                "status": status,
                "started_after": started_after,
                "started_before": started_before,
            },
            group_by=group_by,
            overall=overall,
            yield_percent=_yield_percent(overall),
            groups=groups,
        )

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
    ) -> ResultQuerySummary:
        from nisystemlink.clients.testmonitor.models import QueryResultsRequest, ResultOrderByField

        base = self._result_filter(
            part_number=part_number,
            program_name=program_name,
            serial_number=serial_number,
            status=status,
            started_after=started_after,
            started_before=started_before,
        )
        counts = self._status_counts(base)
        response = self._call(
            self._test.query_results,
            QueryResultsRequest(
                filter=base,
                take=preview_limit,
                order_by=ResultOrderByField.STARTED_AT,
                descending=True,
                return_count=True,
            ),
        )
        results = getattr(response, "results", None) or []
        preview = [self._preview_from_sdk(item) for item in results[:preview_limit]]
        return ResultQuerySummary(
            backend=self.name,
            simulated=False,
            filters={
                "part_number": part_number,
                "program_name": program_name,
                "serial_number": serial_number,
                "status": status,
                "started_after": started_after,
                "started_before": started_before,
            },
            counts=counts,
            yield_percent=_yield_percent(counts),
            matched=counts.total,
            preview_limit=preview_limit,
            preview=preview,
            truncated=counts.total > preview_limit,
        )

    def get_failing_steps(self, result_id: str, preview_limit: int) -> FailingStepsSummary:
        from nisystemlink.clients.testmonitor.models import QueryStepsRequest

        result = self._call(self._test.get_result, result_id)
        fail_filter = f'resultId == "{_escape(result_id)}" && status.statusType == "FAILED"'
        total_filter = f'resultId == "{_escape(result_id)}"'
        failing_page = self._call(
            self._test.query_steps,
            QueryStepsRequest(filter=fail_filter, take=preview_limit, return_count=True),
        )
        total_page = self._call(
            self._test.query_steps,
            QueryStepsRequest(filter=total_filter, take=1, return_count=True),
        )
        failing_count = int(
            getattr(failing_page, "total_count", None) or len(failing_page.steps or [])
        )
        total_count = int(getattr(total_page, "total_count", None) or 0)
        failing_steps = []
        for step in (failing_page.steps or [])[:preview_limit]:
            data = getattr(step, "data", None)
            measurements = []
            for param in getattr(data, "parameters", None) or []:
                measurements.append(
                    MeasurementVsLimit(
                        name=getattr(param, "name", None),
                        status=getattr(param, "status", None),
                        measurement=getattr(param, "measurement", None),
                        low_limit=getattr(param, "lowLimit", None),
                        high_limit=getattr(param, "highLimit", None),
                        units=getattr(param, "units", None),
                    )
                )
            failing_steps.append(
                FailingStep(
                    step_id=getattr(step, "step_id", None),
                    name=getattr(step, "name", None),
                    path=getattr(step, "path", None),
                    status=_enum_value(getattr(getattr(step, "status", None), "status_type", None)),
                    measurements=measurements,
                )
            )
        return FailingStepsSummary(
            backend=self.name,
            simulated=False,
            result=self._preview_from_sdk(result),
            failing_step_count=failing_count,
            total_step_count=total_count,
            preview_limit=preview_limit,
            failing_steps=failing_steps,
            truncated=failing_count > preview_limit,
        )

    def summarize_measurement(
        self,
        *,
        table_id: str | None,
        result_id: str | None,
        preview_limit: int,
    ) -> MeasurementSummary:
        from nisystemlink.clients.dataframe.models import (
            ColumnType,
            DecimationMethod,
            DecimationOptions,
            QueryDecimatedDataRequest,
        )

        resolved = table_id
        if resolved is None and result_id:
            result = self._call(self._test.get_result, result_id)
            ids = [tid for tid in (getattr(result, "data_table_ids", None) or []) if tid]
            if not ids:
                raise BackendError(
                    f"Result {result_id!r} has no attached measurement tables.",
                    code="not_found",
                )
            resolved = ids[0]
        if not resolved:
            raise BackendError(
                "Provide table_id or a result_id that has data_table_ids.",
                code="invalid_request",
            )

        meta = self._call(self._dataframe.get_table_metadata, resolved)
        columns = [
            ColumnInfo(
                name=getattr(col, "name", ""),
                data_type=_enum_value(getattr(col, "data_type", None)),
                column_type=_enum_value(getattr(col, "column_type", None)),
            )
            for col in (getattr(meta, "columns", None) or [])
        ]
        y_column = next(
            (
                col
                for col in (meta.columns or [])
                if getattr(col, "column_type", None) != ColumnType.Index
            ),
            (meta.columns or [None])[0],
        )
        y_name = getattr(y_column, "name", None) if y_column is not None else None
        row_count = int(
            getattr(meta, "row_count", None)
            or getattr(meta, "total_rows", None)
            or getattr(meta, "num_rows", None)
            or 0
        )
        values: list[float] = []
        if y_name:
            decimated = self._call(
                self._dataframe.query_decimated_data,
                resolved,
                QueryDecimatedDataRequest(
                    decimation=DecimationOptions(
                        x_column=None,
                        y_columns=[y_name],
                        intervals=max(preview_limit // 2, 1),
                        method=DecimationMethod.MaxMin,
                    )
                ),
            )
            frame = getattr(decimated, "frame", decimated)
            rows = getattr(frame, "data", None) or []
            for row in rows:
                if not row:
                    continue
                # Index column may be first; pick the last numeric-looking cell.
                cell = row[-1] if len(row) > 1 else row[0]
                try:
                    values.append(float(cell))
                except (TypeError, ValueError):
                    continue
        stats = numeric_stats(values)
        if row_count == 0:
            row_count = int(stats["count"] or 0)
        return MeasurementSummary(
            backend=self.name,
            simulated=False,
            table_id=resolved,
            table_name=getattr(meta, "name", None),
            row_count=row_count,
            columns=columns,
            numeric_column=y_name,
            stats=stats,
            preview_limit=preview_limit,
            preview=downsample(values, preview_limit),
            truncated=row_count > preview_limit,
        )

    def _query_all_assets(self, filter_str: str | None) -> list[Any]:
        from nisystemlink.clients.assetmanagement.models import QueryAssetsRequest

        assets, _total = walk_skip_take(
            lambda **kw: self._call(
                self._assets.query_assets,
                QueryAssetsRequest(
                    filter=filter_str,
                    skip=kw.get("skip"),
                    take=kw.get("take"),
                    return_count=True,
                ),
            ),
            "assets",
        )
        return assets

    def list_calibration_due(
        self,
        *,
        statuses: list[CalStatus] | None,
        preview_limit: int,
    ) -> CalibrationDueSummary:
        wanted = list(statuses or ["APPROACHING_RECOMMENDED_DUE_DATE", "PAST_RECOMMENDED_DUE_DATE"])
        # Official asset examples filter with PascalCase identifiers (AssetIdentifier).
        clauses = [f'CalibrationStatus = "{status}"' for status in wanted]
        filter_str = " || ".join(clauses)
        assets = self._query_all_assets(filter_str)
        by_status: dict[str, int] = {}
        previews = []
        for asset in assets:
            preview = self._asset_preview(asset)
            status = preview.calibration_status or "UNKNOWN"
            by_status[status] = by_status.get(status, 0) + 1
            previews.append(preview)
        return CalibrationDueSummary(
            backend=self.name,
            simulated=False,
            matched=len(previews),
            by_status=by_status,
            preview_limit=preview_limit,
            preview=bounded_preview(previews, preview_limit),
            truncated=len(previews) > preview_limit,
        )

    def query_assets(
        self,
        *,
        name: str | None,
        serial_number: str | None,
        model_name: str | None,
        calibration_status: str | None,
        preview_limit: int,
    ) -> AssetQuerySummary:
        cal_clause = None
        if calibration_status:
            cal_clause = f'CalibrationStatus = "{_escape(calibration_status)}"'
        filter_str = _and(
            [
                _clause("SerialNumber", serial_number),
                f'Name.Contains("{_escape(name)}")' if name else None,
                f'ModelName.Contains("{_escape(model_name)}")' if model_name else None,
                cal_clause,
            ]
        )
        assets = self._query_all_assets(filter_str)
        previews = [self._asset_preview(a) for a in assets]
        return AssetQuerySummary(
            backend=self.name,
            simulated=False,
            filters={
                "name": name,
                "serial_number": serial_number,
                "model_name": model_name,
                "calibration_status": calibration_status,
            },
            matched=len(previews),
            preview_limit=preview_limit,
            preview=bounded_preview(previews, preview_limit),
            truncated=len(previews) > preview_limit,
        )

    def query_systems(
        self,
        *,
        alias: str | None,
        connected: bool | None,
        preview_limit: int,
    ) -> SystemQuerySummary:
        from nisystemlink.clients.systems.models import QuerySystemsRequest

        clauses: list[str | None] = []
        if alias:
            clauses.append(f'(alias.Contains("{_escape(alias)}") || id == "{_escape(alias)}")')
        if connected is True:
            clauses.append("status.data.http_connected == true")
        elif connected is False:
            clauses.append("status.data.http_connected == false")
        filter_str = _and(clauses)
        systems, reported = walk_skip_take(
            lambda **kw: self._call(
                self._systems.query_systems,
                QuerySystemsRequest(
                    filter=filter_str,
                    skip=kw.get("skip"),
                    take=kw.get("take"),
                    projection="new(id, alias, connected, grains, status)",
                ),
            ),
            "data",
            total_count_field="count",
        )
        previews: list[SystemPreview] = []
        connected_count = 0
        for item in systems:
            row = item if isinstance(item, dict) else {}
            sys_id = row.get("id")
            sys_alias = row.get("alias")
            grains = row.get("grains") or {}
            grain_data = grains.get("data") if isinstance(grains, dict) else {}
            os_name = grain_data.get("os") if isinstance(grain_data, dict) else None
            status = row.get("status") or {}
            status_data = status.get("data") if isinstance(status, dict) else {}
            if isinstance(status_data, dict):
                http_connected = status_data.get("http_connected")
            else:
                http_connected = None
            connected_flag = bool(http_connected) if http_connected is not None else None
            if connected_flag:
                connected_count += 1
            previews.append(
                SystemPreview(
                    id=sys_id,
                    alias=sys_alias,
                    connected=connected_flag,
                    os=os_name,
                    status="connected" if connected_flag else "disconnected",
                )
            )
        matched = reported if reported is not None else len(previews)
        return SystemQuerySummary(
            backend=self.name,
            simulated=False,
            filters={"alias": alias, "connected": connected},
            matched=matched,
            connected_count=connected_count,
            preview_limit=preview_limit,
            preview=bounded_preview(previews, preview_limit),
            truncated=matched > preview_limit,
        )

    def query_products(
        self,
        *,
        family: str | None,
        part_number: str | None,
        name: str | None,
        preview_limit: int,
    ) -> ProductQuerySummary:
        from nisystemlink.clients.product.models import QueryProductsRequest

        filter_str = _and(
            [
                _clause("family", family),
                _clause("partNumber", part_number),
                _clause("name", name),
            ]
        )
        products = walk_continuation(
            lambda **kw: self._call(
                self._product.query_products_paged,
                QueryProductsRequest(
                    filter=filter_str,
                    take=kw.get("take"),
                    continuation_token=kw.get("continuation_token"),
                    return_count=True,
                ),
            ),
            "products",
        )
        previews = [
            ProductPreview(
                id=getattr(p, "id", None),
                part_number=getattr(p, "part_number", None),
                name=getattr(p, "name", None),
                family=getattr(p, "family", None),
            )
            for p in products
        ]
        return ProductQuerySummary(
            backend=self.name,
            simulated=False,
            filters={"family": family, "part_number": part_number, "name": name},
            matched=len(previews),
            preview_limit=preview_limit,
            preview=bounded_preview(previews, preview_limit),
            truncated=len(previews) > preview_limit,
        )

    def query_specs(self, product_id: str, preview_limit: int) -> SpecQuerySummary:
        from nisystemlink.clients.spec.models import QuerySpecificationsRequest

        specs = walk_continuation(
            lambda **kw: self._call(
                self._spec.query_specs,
                QuerySpecificationsRequest(
                    product_ids=[product_id],
                    take=kw.get("take"),
                    continuation_token=kw.get("continuation_token"),
                ),
            ),
            "specs",
        )
        previews = []
        for spec in specs:
            limit = getattr(spec, "limit", None)
            previews.append(
                SpecPreview(
                    id=getattr(spec, "id", None),
                    spec_id=getattr(spec, "spec_id", None),
                    product_id=getattr(spec, "product_id", None),
                    name=getattr(spec, "name", None),
                    category=getattr(spec, "category", None),
                    spec_type=_enum_value(getattr(spec, "type", None)),
                    unit=getattr(spec, "unit", None),
                    limit_min=getattr(limit, "min", None) if limit is not None else None,
                    limit_typical=getattr(limit, "typical", None) if limit is not None else None,
                    limit_max=getattr(limit, "max", None) if limit is not None else None,
                )
            )
        return SpecQuerySummary(
            backend=self.name,
            simulated=False,
            product_id=product_id,
            matched=len(previews),
            preview_limit=preview_limit,
            preview=bounded_preview(previews, preview_limit),
            truncated=len(previews) > preview_limit,
        )

    def list_files(
        self,
        *,
        file_ids: list[str] | None,
        result_id: str | None,
        asset_id: str | None,
        preview_limit: int,
    ) -> FileListSummary:
        ids: list[str] = [fid for fid in (file_ids or []) if fid]
        if result_id:
            result = self._call(self._test.get_result, result_id)
            ids.extend(fid for fid in (getattr(result, "file_ids", None) or []) if fid)
        if asset_id:
            from nisystemlink.clients.assetmanagement.models import QueryAssetsRequest

            response = self._call(
                self._assets.query_assets,
                QueryAssetsRequest(filter=f'AssetIdentifier = "{_escape(asset_id)}"', take=1),
            )
            assets = getattr(response, "assets", None) or []
            if not assets:
                raise BackendError(f"Asset {asset_id!r} was not found.", code="not_found")
            ids.extend(fid for fid in (getattr(assets[0], "file_ids", None) or []) if fid)
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return FileListSummary(
                backend=self.name,
                simulated=False,
                matched=0,
                preview_limit=preview_limit,
                preview=[],
                truncated=False,
            )
        response = self._call(self._files.get_files, ids=unique_ids, take=preview_limit)
        available = getattr(response, "available_files", None) or []
        previews = []
        for item in available[:preview_limit]:
            props = getattr(item, "properties", None) or {}
            previews.append(
                FilePreview(
                    id=getattr(item, "id", None),
                    name=props.get("Name") or props.get("name"),
                    size=getattr(item, "size", None) or props.get("Size"),
                    created=_iso(getattr(item, "created", None)),
                )
            )
        return FileListSummary(
            backend=self.name,
            simulated=False,
            matched=len(unique_ids),
            preview_limit=preview_limit,
            preview=previews,
            truncated=len(unique_ids) > preview_limit,
        )

    def update_result_properties(
        self,
        result_id: str,
        *,
        keywords: list[str] | None,
        properties: dict[str, str | None] | None,
    ) -> UpdateResultSummary:
        from nisystemlink.clients.testmonitor.models import UpdateResultRequest

        updated = self._call(
            self._test.update_result,
            UpdateResultRequest(id=result_id, keywords=keywords, properties=properties),
            False,
        )
        return UpdateResultSummary(
            backend=self.name,
            simulated=False,
            result_id=getattr(updated, "id", result_id) or result_id,
            keywords=list(getattr(updated, "keywords", None) or keywords or []),
            properties=dict(getattr(updated, "properties", None) or properties or {}),
        )

    def execute_notebook(
        self,
        notebook_id: str,
        workspace_id: str,
        parameters: dict[str, Any] | None,
    ) -> NotebookExecutionSummary:
        from nisystemlink.clients.notebook.models import CreateExecutionRequest

        string_params = None
        if parameters is not None:
            string_params = {
                key: None if value is None else str(value) for key, value in parameters.items()
            }
        response = self._call(
            self._notebook.create_executions,
            [
                CreateExecutionRequest(
                    notebook_id=notebook_id,
                    workspace_id=workspace_id,
                    parameters=string_params,
                )
            ],
        )
        executions = getattr(response, "executions", None) or []
        first = executions[0] if executions else None
        return NotebookExecutionSummary(
            backend=self.name,
            simulated=False,
            execution_id=getattr(first, "id", None) if first is not None else None,
            notebook_id=notebook_id,
            workspace_id=workspace_id,
            status=_enum_value(getattr(first, "status", None)) if first is not None else None,
            cached_result=getattr(first, "cached_result", None) if first is not None else None,
        )
