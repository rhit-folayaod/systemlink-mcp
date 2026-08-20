"""Pure-Python SystemLink stand-in. No server, no SDK import, deterministic given a seed."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

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
from systemlink_mcp.pagination import chunks
from systemlink_mcp.summarize import bounded_preview, downsample, numeric_stats

ISO = "%Y-%m-%dT%H:%M:%SZ"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    text = ts.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _status_value(status: str | None) -> str:
    return (status or "OTHER").upper()


def _yield_percent(counts: StatusCounts) -> float | None:
    denom = counts.passed + counts.failed
    if denom == 0:
        return None
    return round(100.0 * counts.passed / denom, 2)


def _tally(statuses: list[str]) -> StatusCounts:
    counts = StatusCounts()
    for raw in statuses:
        status = _status_value(raw)
        counts.total += 1
        if status == "PASSED":
            counts.passed += 1
        elif status == "FAILED":
            counts.failed += 1
        elif status == "ERRORED":
            counts.errored += 1
        elif status == "RUNNING":
            counts.running += 1
        else:
            counts.other += 1
    return counts


@dataclass
class FakeMeasurement:
    name: str
    status: str
    measurement: str
    low_limit: str
    high_limit: str
    units: str


@dataclass
class FakeStep:
    step_id: str
    name: str
    path: str
    status: str
    measurements: list[FakeMeasurement] = field(default_factory=list)


@dataclass
class FakeResult:
    id: str
    part_number: str
    serial_number: str
    program_name: str
    status: str
    host_name: str
    started_at: datetime
    total_time_in_seconds: float
    file_ids: list[str] = field(default_factory=list)
    data_table_ids: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    properties: dict[str, str | None] = field(default_factory=dict)
    steps: list[FakeStep] = field(default_factory=list)


@dataclass
class FakeTable:
    id: str
    name: str
    columns: list[ColumnInfo]
    values: list[float]


@dataclass
class FakeAsset:
    id: str
    name: str
    model_name: str
    serial_number: str
    part_number: str
    asset_type: str
    calibration_status: str
    cal_due: datetime | None
    location: str
    presence: str
    keywords: list[str]
    file_ids: list[str] = field(default_factory=list)


@dataclass
class FakeSystem:
    id: str
    alias: str
    connected: bool
    os: str
    status: str


@dataclass
class FakeProduct:
    id: str
    part_number: str
    name: str
    family: str


@dataclass
class FakeSpec:
    id: str
    spec_id: str
    product_id: str
    name: str
    category: str
    spec_type: str
    unit: str
    limit_min: float | None
    limit_typical: float | None
    limit_max: float | None


@dataclass
class FakeFile:
    id: str
    name: str
    size: int
    created: datetime


class _Page:
    def __init__(self, items: list[Any], continuation_token: str | None, total_count: int) -> None:
        self.items = items
        self.results = items
        self.continuation_token = continuation_token
        self.total_count = total_count


class SimulatedBackend:
    """In-memory fleet that looks like a small production test floor."""

    name = "simulated"
    simulated = True

    def __init__(self, seed: int = 42, page_size: int = 100) -> None:
        self.seed = seed
        self.page_size = page_size
        self.pages_fetched = 0
        self._rng = random.Random(seed)
        self.products = self._build_products()
        self.results = self._build_results()
        self.tables = self._build_tables()
        self.assets = self._build_assets()
        self.systems = self._build_systems()
        self.specs = self._build_specs()
        self.files = self._build_files()
        self.executions: list[dict[str, Any]] = []

    def _build_products(self) -> list[FakeProduct]:
        return [
            FakeProduct("prod-5164-a", "PN-5164-A", "PXIe-5164 Digitizer", "Oscilloscopes"),
            FakeProduct("prod-5164-b", "PN-5164-B", "PXIe-5164 Digitizer", "Oscilloscopes"),
            FakeProduct("prod-9045-a", "PN-9045-A", "cRIO-9045 Controller", "CompactRIO"),
        ]

    def _build_results(self) -> list[FakeResult]:
        fail_gain = FakeMeasurement("Gain", "Failed", "1.82", "1.20", "1.50", "mV")
        noise = FakeMeasurement("NoiseFloor", "Passed", "-92.1", "-120", "-80", "dBm")
        fail_result = FakeResult(
            id="res-fail-dut1001",
            part_number="PN-5164-B",
            serial_number="DUT-1001",
            program_name="BoardFunctional.seq",
            status="FAILED",
            host_name="pxi-rack-1",
            started_at=datetime(2026, 6, 15, 14, 32, tzinfo=timezone.utc),
            total_time_in_seconds=87.4,
            file_ids=["file-fail-report"],
            data_table_ids=["tbl-gain-trace"],
            keywords=["nightly", "rev-B"],
            properties={"fixture": "TF-12", "operator": "lab.tech"},
            steps=[
                FakeStep("step-power", "PowerOn", "Main.PowerOn", "PASSED"),
                FakeStep(
                    "step-gain",
                    "Gain",
                    "Main.Analog.Gain",
                    "FAILED",
                    measurements=[fail_gain],
                ),
                FakeStep(
                    "step-noise",
                    "NoiseFloor",
                    "Main.RF.NoiseFloor",
                    "PASSED",
                    measurements=[noise],
                ),
            ],
        )
        pass_result = FakeResult(
            id="res-pass-dut1001",
            part_number="PN-5164-B",
            serial_number="DUT-1001",
            program_name="BoardFunctional.seq",
            status="PASSED",
            host_name="pxi-rack-1",
            started_at=datetime(2026, 6, 14, 9, 5, tzinfo=timezone.utc),
            total_time_in_seconds=64.0,
            data_table_ids=["tbl-gain-trace"],
            keywords=["nightly", "rev-B"],
            steps=[
                FakeStep("step-power", "PowerOn", "Main.PowerOn", "PASSED"),
                FakeStep("step-gain", "Gain", "Main.Analog.Gain", "PASSED"),
            ],
        )

        generated: list[FakeResult] = [fail_result, pass_result]
        part_numbers = ["PN-5164-A", "PN-5164-B", "PN-9045-A"]
        serials = ["DUT-1001", "DUT-1002", "DUT-2001"]
        programs = ["BoardFunctional.seq", "RFSweep.seq"]
        statuses = ["PASSED"] * 7 + ["FAILED"] * 2 + ["ERRORED"]
        start = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)
        for i in range(48):
            status = statuses[self._rng.randrange(len(statuses))]
            started = start + timedelta(days=i * 8, hours=self._rng.randrange(12))
            generated.append(
                FakeResult(
                    id=f"res-gen-{i:03d}",
                    part_number=part_numbers[i % len(part_numbers)],
                    serial_number=serials[i % len(serials)],
                    program_name=programs[i % len(programs)],
                    status=status,
                    host_name="pxi-rack-1" if i % 3 else "criostation-2",
                    started_at=started,
                    total_time_in_seconds=round(30 + self._rng.random() * 90, 2),
                    properties={"revision": "B" if "5164-B" in part_numbers[i % 3] else "A"},
                    steps=[
                        FakeStep(f"s-{i}-1", "Init", "Main.Init", "PASSED"),
                        FakeStep(
                            f"s-{i}-2",
                            "Measure",
                            "Main.Measure",
                            "FAILED" if status == "FAILED" else "PASSED",
                            measurements=[
                                FakeMeasurement(
                                    "Vout",
                                    "Failed" if status == "FAILED" else "Passed",
                                    "1.61" if status == "FAILED" else "1.33",
                                    "1.20",
                                    "1.50",
                                    "mV",
                                )
                            ]
                            if status in {"FAILED", "PASSED"}
                            else [],
                        ),
                    ],
                )
            )
        generated.sort(key=lambda r: r.started_at)
        return generated

    def _build_tables(self) -> dict[str, FakeTable]:
        n = 2000
        values = [math.sin(i / 40.0) + 0.05 * math.sin(i / 7.0) for i in range(n)]
        values[812] = 2.47  # a spike a test engineer would actually ask about
        return {
            "tbl-gain-trace": FakeTable(
                id="tbl-gain-trace",
                name="DUT-1001 Gain Sweep",
                columns=[
                    ColumnInfo(name="sample", data_type="Int32", column_type="Index"),
                    ColumnInfo(name="gain_mV", data_type="Float64", column_type="Data"),
                ],
                values=values,
            )
        }

    def _build_assets(self) -> list[FakeAsset]:
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        return [
            FakeAsset(
                id="asset-6368",
                name="DAQ in PXISlot2",
                model_name="NI PXIe-6368",
                serial_number="01BB877A",
                part_number="PXIe-6368",
                asset_type="DEVICE",
                calibration_status="PAST_RECOMMENDED_DUE_DATE",
                cal_due=now - timedelta(days=40),
                location="pxi-rack-1",
                presence="PRESENT",
                keywords=["pxi", "daq"],
                file_ids=["file-cal-cert-6368"],
            ),
            FakeAsset(
                id="asset-5163",
                name="Scope in PXISlot4",
                model_name="NI PXIe-5163",
                serial_number="02CC988B",
                part_number="PXIe-5163",
                asset_type="DEVICE",
                calibration_status="APPROACHING_RECOMMENDED_DUE_DATE",
                cal_due=now + timedelta(days=12),
                location="pxi-rack-1",
                presence="PRESENT",
                keywords=["pxi", "scope"],
            ),
            FakeAsset(
                id="asset-1085",
                name="PXI Chassis",
                model_name="NI PXIe-1085",
                serial_number="CH-1085-01",
                part_number="PXIe-1085",
                asset_type="CHASSIS",
                calibration_status="OK",
                cal_due=now + timedelta(days=210),
                location="pxi-rack-1",
                presence="PRESENT",
                keywords=["pxi", "chassis"],
            ),
            FakeAsset(
                id="asset-6001",
                name="USB DAQ spare",
                model_name="NI USB-6001",
                serial_number="USB-6001-77",
                part_number="USB-6001",
                asset_type="DEVICE",
                calibration_status="OUT_FOR_CALIBRATION",
                cal_due=now + timedelta(days=5),
                location="metrology",
                presence="NOT_PRESENT",
                keywords=["usb", "spare"],
            ),
        ]

    def _build_systems(self) -> list[FakeSystem]:
        return [
            FakeSystem("pxi-rack-1", "PXI Rack 1", True, "Windows", "healthy"),
            FakeSystem(
                "criostation-2",
                "cRIO Station 2",
                False,
                "NI Linux Real-Time",
                "disconnected",
            ),
            FakeSystem("bench-lab-3", "Bench Lab 3", True, "Windows", "healthy"),
        ]

    def _build_specs(self) -> list[FakeSpec]:
        return [
            FakeSpec(
                "spec-gain",
                "spec-gain",
                "PN-5164-B",
                "output voltage",
                "Parametric Specs",
                "PARAMETRIC",
                "mV",
                1.2,
                1.35,
                1.5,
            ),
            FakeSpec(
                "spec-vin",
                "spec-vin",
                "PN-5164-B",
                "input voltage",
                "Parametric Specs",
                "PARAMETRIC",
                "mV",
                0.02,
                0.08,
                0.15,
            ),
            FakeSpec(
                "spec-boot",
                "spec-boot",
                "PN-9045-A",
                "controller boot",
                "Functional Specs",
                "FUNCTIONAL",
                None,
                None,
                None,
                None,
            ),
        ]

    def _build_files(self) -> list[FakeFile]:
        created = datetime(2026, 6, 15, 14, 40, tzinfo=timezone.utc)
        return [
            FakeFile("file-fail-report", "DUT-1001-BoardFunctional.html", 184320, created),
            FakeFile("file-cal-cert-6368", "PXIe-6368-cal-cert.pdf", 65536, created),
        ]

    def _paged(self, items: list[Any], continuation_token: str | None = None) -> _Page:
        pages = chunks(items, self.page_size) or [[]]
        index = 0
        if continuation_token:
            try:
                index = int(continuation_token)
            except ValueError:
                index = 0
        self.pages_fetched += 1
        page_items = pages[index] if index < len(pages) else []
        next_token = str(index + 1) if index + 1 < len(pages) else None
        return _Page(page_items, next_token, len(items))

    def walk_all(self, items: list[Any]) -> list[Any]:
        """Walk every simulated page so tests can assert pagination was not skipped."""
        collected: list[Any] = []
        token: str | None = None
        while True:
            page = self._paged(items, token)
            collected.extend(page.items)
            if not page.continuation_token:
                return collected
            token = page.continuation_token

    def _match_result(
        self,
        result: FakeResult,
        *,
        part_number: str | None,
        program_name: str | None,
        serial_number: str | None,
        status: str | None,
        started_after: str | None,
        started_before: str | None,
    ) -> bool:
        if part_number and result.part_number != part_number:
            return False
        if program_name and result.program_name != program_name:
            return False
        if serial_number and result.serial_number != serial_number:
            return False
        if status and _status_value(result.status) != _status_value(status):
            return False
        after = _parse(started_after)
        before = _parse(started_before)
        if after and result.started_at < after:
            return False
        if before and result.started_at > before:
            return False
        return True

    def _filtered_results(self, **filters: Any) -> list[FakeResult]:
        return [r for r in self.results if self._match_result(r, **filters)]

    def _preview_result(self, result: FakeResult) -> ResultPreview:
        return ResultPreview(
            id=result.id,
            part_number=result.part_number,
            serial_number=result.serial_number,
            program_name=result.program_name,
            status=result.status,
            host_name=result.host_name,
            started_at=_iso(result.started_at),
            total_time_in_seconds=result.total_time_in_seconds,
            file_ids=list(result.file_ids),
            data_table_ids=list(result.data_table_ids),
        )

    def _preview_asset(self, asset: FakeAsset) -> AssetPreview:
        return AssetPreview(
            id=asset.id,
            name=asset.name,
            model_name=asset.model_name,
            serial_number=asset.serial_number,
            part_number=asset.part_number,
            asset_type=asset.asset_type,
            calibration_status=asset.calibration_status,
            cal_due=_iso(asset.cal_due) if asset.cal_due else None,
            location=asset.location,
            presence=asset.presence,
            keywords=list(asset.keywords),
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
        matched = self.walk_all(
            self._filtered_results(
                part_number=part_number,
                program_name=program_name,
                serial_number=serial_number,
                status=status,
                started_after=started_after,
                started_before=started_before,
            )
        )
        overall = _tally([r.status for r in matched])
        groups: list[YieldGroup] = []
        if group_by != "none":
            buckets: dict[str, list[str]] = {}
            for result in matched:
                key = getattr(result, group_by) or "(none)"
                buckets.setdefault(key, []).append(result.status)
            for key, statuses in sorted(buckets.items()):
                counts = _tally(statuses)
                groups.append(
                    YieldGroup(key=key, counts=counts, yield_percent=_yield_percent(counts))
                )
        return YieldSummary(
            backend=self.name,
            simulated=True,
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
        matched = self.walk_all(
            self._filtered_results(
                part_number=part_number,
                program_name=program_name,
                serial_number=serial_number,
                status=status,
                started_after=started_after,
                started_before=started_before,
            )
        )
        ordered = sorted(matched, key=lambda r: r.started_at, reverse=True)
        preview = [self._preview_result(r) for r in bounded_preview(ordered, preview_limit)]
        counts = _tally([r.status for r in matched])
        return ResultQuerySummary(
            backend=self.name,
            simulated=True,
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
            matched=len(matched),
            preview_limit=preview_limit,
            preview=preview,
            truncated=len(ordered) > preview_limit,
        )

    def get_failing_steps(self, result_id: str, preview_limit: int) -> FailingStepsSummary:
        result = next((r for r in self.results if r.id == result_id), None)
        if result is None:
            raise BackendError(f"Result {result_id!r} was not found.", code="not_found")
        failing = [s for s in result.steps if _status_value(s.status) == "FAILED"]
        preview_steps = []
        for step in bounded_preview(failing, preview_limit):
            preview_steps.append(
                FailingStep(
                    step_id=step.step_id,
                    name=step.name,
                    path=step.path,
                    status=step.status,
                    measurements=[
                        MeasurementVsLimit(
                            name=m.name,
                            status=m.status,
                            measurement=m.measurement,
                            low_limit=m.low_limit,
                            high_limit=m.high_limit,
                            units=m.units,
                        )
                        for m in step.measurements
                    ],
                )
            )
        return FailingStepsSummary(
            backend=self.name,
            simulated=True,
            result=self._preview_result(result),
            failing_step_count=len(failing),
            total_step_count=len(result.steps),
            preview_limit=preview_limit,
            failing_steps=preview_steps,
            truncated=len(failing) > preview_limit,
        )

    def summarize_measurement(
        self,
        *,
        table_id: str | None,
        result_id: str | None,
        preview_limit: int,
    ) -> MeasurementSummary:
        resolved = table_id
        if resolved is None and result_id:
            result = next((r for r in self.results if r.id == result_id), None)
            if result is None:
                raise BackendError(f"Result {result_id!r} was not found.", code="not_found")
            if not result.data_table_ids:
                raise BackendError(
                    f"Result {result_id!r} has no attached measurement tables.",
                    code="not_found",
                )
            resolved = result.data_table_ids[0]
        if not resolved:
            raise BackendError(
                "Provide table_id or a result_id that has data_table_ids.",
                code="invalid_request",
            )
        table = self.tables.get(resolved)
        if table is None:
            raise BackendError(f"Measurement table {resolved!r} was not found.", code="not_found")
        stats = numeric_stats(table.values)
        preview = downsample(table.values, preview_limit)
        return MeasurementSummary(
            backend=self.name,
            simulated=True,
            table_id=table.id,
            table_name=table.name,
            row_count=len(table.values),
            columns=list(table.columns),
            numeric_column="gain_mV",
            stats=stats,
            preview_limit=preview_limit,
            preview=preview,
            truncated=len(table.values) > preview_limit,
        )

    def list_calibration_due(
        self,
        *,
        statuses: list[CalStatus] | None,
        preview_limit: int,
    ) -> CalibrationDueSummary:
        wanted = set(statuses or ["APPROACHING_RECOMMENDED_DUE_DATE", "PAST_RECOMMENDED_DUE_DATE"])
        matched = self.walk_all([a for a in self.assets if a.calibration_status in wanted])
        by_status: dict[str, int] = {}
        for asset in matched:
            by_status[asset.calibration_status] = by_status.get(asset.calibration_status, 0) + 1
        ordered = sorted(
            matched,
            key=lambda a: a.cal_due or datetime.max.replace(tzinfo=timezone.utc),
        )
        return CalibrationDueSummary(
            backend=self.name,
            simulated=True,
            matched=len(matched),
            by_status=by_status,
            preview_limit=preview_limit,
            preview=[self._preview_asset(a) for a in bounded_preview(ordered, preview_limit)],
            truncated=len(ordered) > preview_limit,
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
        matched = []
        for asset in self.assets:
            if name and name.lower() not in asset.name.lower():
                continue
            if serial_number and asset.serial_number != serial_number:
                continue
            if model_name and model_name.lower() not in asset.model_name.lower():
                continue
            if calibration_status and asset.calibration_status != calibration_status:
                continue
            matched.append(asset)
        matched = self.walk_all(matched)
        return AssetQuerySummary(
            backend=self.name,
            simulated=True,
            filters={
                "name": name,
                "serial_number": serial_number,
                "model_name": model_name,
                "calibration_status": calibration_status,
            },
            matched=len(matched),
            preview_limit=preview_limit,
            preview=[self._preview_asset(a) for a in bounded_preview(matched, preview_limit)],
            truncated=len(matched) > preview_limit,
        )

    def query_systems(
        self,
        *,
        alias: str | None,
        connected: bool | None,
        preview_limit: int,
    ) -> SystemQuerySummary:
        matched = []
        for system in self.systems:
            if alias and alias.lower() not in system.alias.lower() and alias != system.id:
                continue
            if connected is not None and system.connected != connected:
                continue
            matched.append(system)
        matched = self.walk_all(matched)
        preview = [
            SystemPreview(
                id=s.id,
                alias=s.alias,
                connected=s.connected,
                os=s.os,
                status=s.status,
            )
            for s in bounded_preview(matched, preview_limit)
        ]
        return SystemQuerySummary(
            backend=self.name,
            simulated=True,
            filters={"alias": alias, "connected": connected},
            matched=len(matched),
            connected_count=sum(1 for s in matched if s.connected),
            preview_limit=preview_limit,
            preview=preview,
            truncated=len(matched) > preview_limit,
        )

    def query_products(
        self,
        *,
        family: str | None,
        part_number: str | None,
        name: str | None,
        preview_limit: int,
    ) -> ProductQuerySummary:
        matched = []
        for product in self.products:
            if family and product.family != family:
                continue
            if part_number and product.part_number != part_number:
                continue
            if name and name.lower() not in product.name.lower():
                continue
            matched.append(product)
        matched = self.walk_all(matched)
        preview = [
            ProductPreview(
                id=p.id, part_number=p.part_number, name=p.name, family=p.family
            )
            for p in bounded_preview(matched, preview_limit)
        ]
        return ProductQuerySummary(
            backend=self.name,
            simulated=True,
            filters={"family": family, "part_number": part_number, "name": name},
            matched=len(matched),
            preview_limit=preview_limit,
            preview=preview,
            truncated=len(matched) > preview_limit,
        )

    def query_specs(self, product_id: str, preview_limit: int) -> SpecQuerySummary:
        matched = self.walk_all(
            [s for s in self.specs if s.product_id == product_id or s.id == product_id]
        )
        preview = [
            SpecPreview(
                id=s.id,
                spec_id=s.spec_id,
                product_id=s.product_id,
                name=s.name,
                category=s.category,
                spec_type=s.spec_type,
                unit=s.unit,
                limit_min=s.limit_min,
                limit_typical=s.limit_typical,
                limit_max=s.limit_max,
            )
            for s in bounded_preview(matched, preview_limit)
        ]
        return SpecQuerySummary(
            backend=self.name,
            simulated=True,
            product_id=product_id,
            matched=len(matched),
            preview_limit=preview_limit,
            preview=preview,
            truncated=len(matched) > preview_limit,
        )

    def list_files(
        self,
        *,
        file_ids: list[str] | None,
        result_id: str | None,
        asset_id: str | None,
        preview_limit: int,
    ) -> FileListSummary:
        ids: list[str] = list(file_ids or [])
        if result_id:
            result = next((r for r in self.results if r.id == result_id), None)
            if result is None:
                raise BackendError(f"Result {result_id!r} was not found.", code="not_found")
            ids.extend(result.file_ids)
        if asset_id:
            asset = next((a for a in self.assets if a.id == asset_id), None)
            if asset is None:
                raise BackendError(f"Asset {asset_id!r} was not found.", code="not_found")
            ids.extend(asset.file_ids)
        wanted = set(ids)
        matched = self.walk_all([f for f in self.files if not wanted or f.id in wanted])
        preview = [
            FilePreview(id=f.id, name=f.name, size=f.size, created=_iso(f.created))
            for f in bounded_preview(matched, preview_limit)
        ]
        return FileListSummary(
            backend=self.name,
            simulated=True,
            matched=len(matched),
            preview_limit=preview_limit,
            preview=preview,
            truncated=len(matched) > preview_limit,
        )

    def update_result_properties(
        self,
        result_id: str,
        *,
        keywords: list[str] | None,
        properties: dict[str, str | None] | None,
    ) -> UpdateResultSummary:
        result = next((r for r in self.results if r.id == result_id), None)
        if result is None:
            raise BackendError(f"Result {result_id!r} was not found.", code="not_found")
        if keywords is not None:
            result.keywords = list(keywords)
        if properties is not None:
            result.properties.update(properties)
        return UpdateResultSummary(
            backend=self.name,
            simulated=True,
            result_id=result.id,
            keywords=list(result.keywords),
            properties=dict(result.properties),
        )

    def execute_notebook(
        self,
        notebook_id: str,
        workspace_id: str,
        parameters: dict[str, Any] | None,
    ) -> NotebookExecutionSummary:
        execution_id = f"exec-{uuid4().hex[:12]}"
        record = {
            "id": execution_id,
            "notebook_id": notebook_id,
            "workspace_id": workspace_id,
            "parameters": parameters or {},
            "status": "QUEUED",
        }
        self.executions.append(record)
        return NotebookExecutionSummary(
            backend=self.name,
            simulated=True,
            execution_id=execution_id,
            notebook_id=notebook_id,
            workspace_id=workspace_id,
            status="QUEUED",
            cached_result=False,
        )
