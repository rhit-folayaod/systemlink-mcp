# systemlink-mcp development log

This log records problems that actually came up while building the server, the
evidence used to decide, and the fix. It is written so it can be cited later.
Dates are 2026-08-20 unless noted.

Sources used for SDK facts:

- [python-docs.systemlink.io stable index](https://python-docs.systemlink.io/en/stable/index.html)
- [Getting Started](https://python-docs.systemlink.io/en/stable/getting_started.html)
- [nisystemlink.clients.core](https://python-docs.systemlink.io/en/stable/api_reference/core.html)
- [QueryResultsRequest source](https://python-docs.systemlink.io/en/stable/_modules/nisystemlink/clients/testmonitor/models/_query_results_request.html)
- GitHub source for `ni/nisystemlink-clients-python` (client classes under `nisystemlink/clients/`)
- PyPI: `nisystemlink-clients` 2.32.1, MIT, CPython 3.10+

Companion project used for MCP/layout conventions: `rhit-folayaod/daq-mcp`.

---

## Phase 1 — SDK discovery

### Problem 1 — Do not invent client classes

The build prompt forbids writing `nisystemlink-clients` calls from memory. The
package is split by service area, and guessing class names produces code that
looks right and fails at import time.

**Evidence.** The stable docs index lists these API modules: `core`, `alarm`,
`tag`, `product`, `testmonitor`, `dataframe`, `spec`, `file`, `notebook`,
`feeds`, `assetmanagement`, `systems`, `work_item`, `artifact`, `notification`.
The GitHub tree under `nisystemlink/clients/` matches that list. Primary entry
points from Getting Started:

| Area | Class | Read operations used here | Mutating operations |
| --- | --- | --- | --- |
| Test Monitor | `TestMonitorClient` | `get_results`, `get_result`, `query_results`, `query_result_values`, `get_steps`, `get_step`, `query_steps`, `query_step_values`, `api_info` | `create_*`, `update_*`, `delete_*` for results and steps |
| Product | `ProductClient` | `get_products_paged`, `get_product`, `query_products_paged`, `query_product_values` | `create_products`, `update_products`, `delete_product(s)` |
| DataFrame | `DataFrameClient` | `list_tables`, `query_tables`, `get_table_metadata`, `get_table_data`, `query_table_data`, `query_decimated_data`, `export_table_data` | `create_table`, `append_table_data`, `modify_table(s)`, `delete_table(s)` |
| Spec | `SpecClient` | `query_specs`, `get_spec`, `api_info` | `create_specs`, `update_specs`, `delete_specs` |
| File | `FileClient` | `get_files`, `query_files_linq`, `search_files`, `download_file`, `api_info` | `upload_file`, chunked upload session, `update_metadata`, `delete_file(s)` |
| Notebook | `NotebookClient` | `get_notebook`, `query_notebooks`, `get_notebook_content`, `get_execution_by_id`, `query_executions` | `create_notebook`, `update_notebook`, `delete_notebook`, `create_executions`, `retry_executions`, `cancel_executions` |
| Asset Management | `AssetManagementClient` | `query_assets`, `query_asset_utilization_history` | `create_assets`, `delete_assets`, `link_files`, `start_utilization`, `end_utilization`, `utilization_heartbeat` |
| Systems | `SystemsClient` | `query_systems` | `create_virtual_system`, `remove_systems` |
| Tag | `TagManager` | `open`, `query`, `read`, selections | `update`, `delete`, `create_writer` |
| Alarm | `AlarmClient` | `query_alarms`, `get_alarm` | `create_or_update_alarm`, `acknowledge_alarms`, `delete_alarm(s)` |

Work items, test plans, feeds, artifacts, and notifications exist in the SDK
and were left out of the first tool surface. They are not what a test engineer
asks first, and several of them are closer to the generic work-order shape the
project is trying not to copy.

**Solution.** Tools were chosen only where the Getting Started docs plus the
client source showed a real method. The implemented list is in the README.

### Problem 2 — Authentication is not a custom header we invent

**Evidence.** `HttpConfiguration.__init__` takes `server_uri`, optional
`api_key`, or `username`+`password`, plus `workspace` and `verify`. The API key
is stored under header name `x-ni-api-key`. Getting Started examples construct:

```python
HttpConfiguration(server_uri="https://yourserver.yourcompany.com", api_key="")
```

If `configuration` is omitted, each client calls
`HttpConfigurationManager.get_configuration()`, which is how SystemLink Client
and SystemLink Jupyter environments inject connection info. `ApiException`
exposes `http_status_code`, `message`, and `error`.

**Solution.** Real backend: env URI+key/password first; otherwise
`HttpConfigurationManager`; wrap `ApiException` as a structured error dict.
Never let a traceback become the tool result.

### Problem 3 — List APIs are paged, and the paging style is not uniform

**Evidence.**

- Test Monitor `get_results` / `query_results` and Product
  `get_products_paged` / `query_products_paged` use `continuation_token` +
  `take`. Official examples loop `while response.continuation_token`.
- Specs use `continuation_token` on `query_specs`.
- DataFrame `list_tables` / `get_table_data` use `continuation_token`.
- Assets use `skip` / `take` on `QueryAssetsRequest`.
- Systems use `skip` / `take` on `QuerySystemsRequest`; the response is
  `QuerySystemsResponse(count, data: list[dict])`, not a typed System model.
- Files `get_files` uses `skip` / `take`.
- The SDK already has `nisystemlink.clients.core.helpers.paginate` for
  continuation tokens. Our backends cannot import that helper from tool code,
  and the simulated backend has no SDK, so pagination lives in
  `systemlink_mcp.pagination`.

**Solution.** Any list/query backend method walks pages until the token is
empty or skip covers `total_count`. Tests force `page_size=2` on the simulator
and assert `pages_fetched > 1` while the matched count still equals the full
in-memory set.

### Problem 4 — Yield has no aggregation endpoint

**Evidence.** `QueryResultsRequest` documents Dynamic LINQ fields including
`status.statusType`, `partNumber`, `programName`, `serialNumber`, `startedAt`.
`return_count=True` makes the service include a count of all matches; the
docstring warns that counting may walk the database. There is no documented
"group by part number" aggregate RPC.

**Solution.** For overall yield, the real backend issues `query_results` with
`take=1` and `return_count=True` once per status type (PASSED, FAILED, ERRORED,
RUNNING) plus a total. That is count-without-payload, not "download every
result and tally locally." Group-by still has to page projected rows
(status + the group field only). The simulator tallies in memory after walking
its own pages so both backends share the same summary shape.

### Problem 5 — Measurement tables are unbounded

**Evidence.** Getting Started shows `query_decimated_data` with
`DecimationOptions(method=DecimationMethod.MaxMin, intervals=...)` and
`get_table_data` / `export_table_data` which can return or stream large row
sets (the export example uses `take=100000`).

**Solution.** `summarize_measurement` uses `query_decimated_data` on the real
backend and downsample+stats on the simulator. The 2000-point simulated gain
trace is there so tests can assert `len(preview) <= preview_limit` while
`row_count == 2000`.

### Problem 6 — AssetManagementClient cannot update an asset

**Evidence.** `_asset_management_client.py` in 2.32.x defines `create_assets`,
`query_assets`, `delete_assets`, `link_files`, `query_asset_utilization_history`,
`start_utilization`, `end_utilization`, `utilization_heartbeat`. There is no
`update_assets`.

**Solution.** The gated write tools are `update_result_properties`
(`TestMonitorClient.update_result`) and `execute_notebook`
(`NotebookClient.create_executions`). The README states this limit instead of
pretending asset patch exists.

### Proposed tool surface (the list that was implemented)

Read-only: `summarize_yield`, `query_results`, `get_failing_steps`,
`summarize_measurement`, `list_calibration_due`, `query_assets`,
`query_systems`, `query_products`, `query_specs`, `list_files`.

Gated: `update_result_properties`, `execute_notebook`.

The original prompt asked to pause here for review. Implementation continued
in the same session because the user asked to plan and implement, keep this
log, and push incrementally.

---

## Phase 2 — Project setup

### Problem 7 — Tools must not import the SDK

Same rule as `daq-mcp`'s `DAQBackend`. If tools import
`nisystemlink.clients.*`, tests cannot run without constructing HTTP clients
and the simulator cannot stay a drop-in.

**Solution.** `SystemLinkBackend` protocol in `backend.py`. `server.py` calls
`get_backend()`. `nisystemlink-clients` is imported only inside `real.py`.
`SYSTEMLINK_MCP_SIMULATE=1` short-circuits to `SimulatedBackend`. Otherwise
`RealBackend` is constructed and, on any connect failure, the selector logs to
stderr and falls back to simulation.

`uv_build` + `src/systemlink_mcp` matches `daq-mcp`. LICENSE is Apache 2.0;
NOTICE attributes David Oluwatimilehin "Timi" Folayan.

---

## Phase 3 — Simulated backend

### Problem 8 — Empty fakes would not test summarization or filters

A backend that returns `[]` makes every test pass without proving preview
caps, date windows, or failing-step measurements.

**Solution.** Seed 42 builds a small but populated floor: three products (two
oscilloscope revisions and a cRIO), 50 results spanning 2025–2026, a 2000-point
gain trace with a planted spike at index 812 (`2.47`), four assets with all
four `CalibrationStatus` values from the SDK enum, three systems, specs for
PN-5164-B, and files linked to the failing DUT result. Fixture ids such as
`res-fail-dut1001` and `tbl-gain-trace` are stable so tests can assert exact
measurement-versus-limit numbers.

Internal `page_size` defaults to 100 and is set to 2 in pagination tests so
the walk is observable.

---

## Phase 4 — Real backend

### Problem 9 — Dynamic LINQ field names are camelCase in Test Monitor and PascalCase in the asset example

**Evidence.** `QueryResultsRequest.filter` documents `partNumber`,
`programName`, `serialNumber`, `status.statusType`, `startedAt`. The official
Test Monitor example uses `status.statusType="{status_type.value}"` and
`programName="{program_name}"`. The official asset example uses
`AssetIdentifier = "{created_asset_id}"` (PascalCase, `=` not `==`). Systems
docs use `id`, `alias`, `status.data.http_connected`. Specs use `product_ids`
as a required list plus optional Dynamic LINQ on `specId`, `name`, etc.

**Solution.** Test Monitor / product / spec filters use the documented camelCase
fields and `==`. Asset filters use PascalCase identifiers consistent with
`AssetIdentifier`. This split is easy to get wrong; it is also what the
published examples actually do. Live-server rejection of `CalibrationStatus`
would mean the filter string needs to be adjusted to whatever that deployment
accepts — CI cannot prove that string because it never calls a live server.

### Problem 10 — Systems are dicts, not a typed model

**Evidence.** `QuerySystemsResponse.data` is `List[Dict[str, Any]]`. The
example projects `new(id, alias)`.

**Solution.** Real backend requests
`projection="new(id, alias, connected, grains, status)"` and reads
`status.data.http_connected` and `grains.data.os` defensively. Missing keys
become `None` instead of raising.

---

## Phase 5 — Tools

### Problem 11 — FastMCP transport naming

Standalone FastMCP uses `@mcp.tool` (no call required) and
`mcp.run(transport="http")` for Streamable HTTP. The in-SDK FastMCP uses
`transport="streamable-http"`. SSE is deprecated in the 2025-03-26 MCP spec.

**Solution.** Depend on the standalone `fastmcp` package. Default stdio;
`--http` selects Streamable HTTP. No `transport="sse"`.

### Problem 12 — Write gating must be checked per call, not at import

If `WRITES_ENABLED` is captured at import, pytest cannot enable writes with
`monkeypatch.setenv` after `server` is imported.

**Solution.** `_writes_enabled()` calls `load_settings()` on every mutating
tool invocation. `reset_backend()` clears the singleton so tests that mutate
simulated results start clean.

---

## Phase 6 — Tests

### Problem 13 — Tool objects from FastMCP may or may not stay directly callable

If `@mcp.tool` replaced the function with a non-callable wrapper, importing
`server.summarize_yield` in pytest would fail.

**Outcome.** Tests call the decorated functions as ordinary Python callables
and validate payloads with the same Pydantic models the backend returns. If a
future FastMCP version wraps them, unwrap `.fn` (or equivalent) rather than
duplicating tool logic.

### Problem 14 — Simulator method used as a free function

List comprehensions in `query_results` called `_preview_result(r)` instead of
`self._preview_result(r)`. Pytest failed immediately with `NameError`. This is
the kind of bug a paper should record: the method existed, the name was right,
and Python still treated it as a missing global because it was inside a
comprehension in an instance method.

**Solution.** Call `self._preview_result`. Ruff `F821` would have caught this
before pytest if lint ran first; CI now runs ruff then pytest.

---

## Phase 7 — Docs and Inspector

Inspector command used in the README:

```text
SYSTEMLINK_MCP_SIMULATE=1 npx -y @modelcontextprotocol/inspector uv run server.py
```

That is the documented Inspector pattern of passing the server launch command
as arguments to `@modelcontextprotocol/inspector`. This log does not claim the
Inspector was run in a browser in CI; it records the command that should be
used locally.
