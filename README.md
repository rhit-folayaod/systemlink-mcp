# systemlink-mcp

An MCP server that exposes **NI SystemLink** to AI clients with
test-and-measurement-native tools: yield by product revision, failing DUT
steps versus spec limits, measurement-trace summaries, and calibration-due
assets.

This is the fleet/data-layer companion to [`daq-mcp`](https://github.com/rhit-folayaod/daq-mcp).
`daq-mcp` talks to a single DAQ device. `systemlink-mcp` queries and correlates
results, assets, and systems across a lab.

This is a personal project with one maintainer. It is **not** an NI product and
is not affiliated with, endorsed by, or supported by NI / Emerson.

## Why test-domain tools instead of a generic wrapper

A commercial middleware catalog can list SystemLink among thousands of
industrial systems and then expose a CMMS/ERP-shaped surface: work orders,
sites, spaces, sales orders. That abstraction cannot answer what a test
engineer actually asks:

- pass/fail rates for product revision B this quarter
- which measurement on a failing DUT exceeded its limit
- which PXI module is past its calibration due date

Every tool here is shaped around those questions. Responses are **summaries
plus a bounded preview**, not raw Test Monitor or DataFrame payloads. Dumping
thousands of result rows or a 2000-point waveform into a model context makes
the server unusable even if the API calls are correct.

## Read-only by default

Mutating tools (`update_result_properties`, `execute_notebook`) refuse unless
`SYSTEMLINK_MCP_ALLOW_WRITE=1`. Read tools work with no extra flags.

The current `nisystemlink-clients` `AssetManagementClient` has create, query,
delete, file-link, and utilization methods, but **no asset update method**. The
gated write surface therefore updates Test Monitor result metadata and queues
notebook executions, not asset records.

## Stack

Python 3.11+, `uv`, FastMCP (stdio or Streamable HTTP), Pydantic v2, and the
official `nisystemlink-clients` package. Tools never import the SDK; they call
a `SystemLinkBackend`. Set `SYSTEMLINK_MCP_SIMULATE=1` for a pure-Python fake
that needs no SystemLink server.

## Quick start (no SystemLink server)

```bash
uv sync
SYSTEMLINK_MCP_SIMULATE=1 uv run server.py
```

PowerShell:

```powershell
$env:SYSTEMLINK_MCP_SIMULATE="1"; uv run server.py
```

## Real SystemLink

The simulated backend is only used when `SYSTEMLINK_MCP_SIMULATE=1`, or when a
live connect fails and `SYSTEMLINK_MCP_REQUIRE_REAL` is unset. A live response
has `"backend": "systemlink"` and `"simulated": false`.

1. Create an API key in SystemLink (Enterprise: a policy, then an API key; the
   HTTP header the SDK sends is `x-ni-api-key`). SLE does not accept username
   and password for programmatic access.
2. Copy `.env.example` to `.env` (gitignored) and set:

```text
SYSTEMLINK_SERVER_URI=https://your-systemlink-host
SYSTEMLINK_API_KEY=...
SYSTEMLINK_MCP_REQUIRE_REAL=1
```

Leave `SYSTEMLINK_MCP_SIMULATE` unset. On-prem SystemLink Server that still
allows basic auth can use `SYSTEMLINK_USERNAME` / `SYSTEMLINK_PASSWORD`
instead of a key. SystemLink Cloud can use the API key alone (no URI); the
backend then constructs `CloudHttpConfiguration`.

3. Probe before MCP so a failed login cannot silently become the fake fleet:

```powershell
uv run server.py --probe
```

You should see `"simulated": false` and a short `query_systems` preview from
your server. Then launch MCP without the simulate flag, or point Cursor at
`.cursor/mcp.json.live.example`.

If `--probe` fails, the JSON error is from the SDK (`ApiException` / connect),
not from dummy data.

## Tools

| Tool | Access | What it answers |
| --- | --- | --- |
| `summarize_yield` | read | Pass/fail yield, optionally grouped by part, program, or serial |
| `query_results` | read | Filtered result counts plus a bounded recent preview |
| `get_failing_steps` | read | Failing steps for one result, with measurement vs limits |
| `summarize_measurement` | read | Table stats plus a downsampled trace (decimated on the live API) |
| `list_calibration_due` | read | Assets past or approaching calibration due |
| `query_assets` | read | Inventory with presence and calibration status |
| `query_systems` | read | Registered systems and connection health |
| `query_products` | read | Products / part numbers / families |
| `query_specs` | read | Spec limits for a product |
| `list_files` | read | File metadata for a result or asset (no contents) |
| `update_result_properties` | gated write | Keywords/properties on a test result |
| `execute_notebook` | gated write | Queue a Jupyter notebook execution |

## MCP Inspector

From the repo root, with simulation forced:

```bash
SYSTEMLINK_MCP_SIMULATE=1 npx -y @modelcontextprotocol/inspector uv run server.py
```

PowerShell:

```powershell
$env:SYSTEMLINK_MCP_SIMULATE="1"
npx -y @modelcontextprotocol/inspector uv run server.py
```

Open the printed URL (typically `http://127.0.0.1:6274`). Confirm the twelve
tools appear, then call `summarize_yield` with `group_by=part_number`.

Streamable HTTP instead of stdio:

```bash
SYSTEMLINK_MCP_SIMULATE=1 uv run server.py --http
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Do not use `transport="sse"`;
that transport is deprecated.

## Cursor

Use `.cursor/mcp.json.example` (simulator) or `.cursor/mcp.json.live.example`
(real server). Machine-local `.cursor/mcp.json` is gitignored.

```json
{
  "mcpServers": {
    "systemlink-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:/Users/folayaod/personal/systemlink-mcp",
        "server.py"
      ],
      "env": {
        "SYSTEMLINK_MCP_SIMULATE": "1"
      }
    }
  }
}
```

## Claude Desktop

Same JSON block, in `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Restart Claude Desktop after saving.

## Prompts that exercise the simulated fleet

1. "What is the pass/fail yield for part number PN-5164-B, grouped against the other revisions?"
2. "DUT-1001 failed BoardFunctional.seq. Which step failed, and how did Gain compare to its limits? Summarize the attached measurement trace."
3. "Which PXI assets are overdue or approaching calibration, and is PXI Rack 1 connected?"

## Tests

```bash
SYSTEMLINK_MCP_SIMULATE=1 uv run pytest
```

Tests hit the simulated backend only. They do not open a network connection to
SystemLink.

## Status

Personal project, one maintainer. The simulated backend is the supported
day-to-day path. The real backend is written against `nisystemlink-clients`
2.32.x APIs documented at [python-docs.systemlink.io](https://python-docs.systemlink.io)
and the [ni/nisystemlink-clients-python](https://github.com/ni/nisystemlink-clients-python)
source. It has not been run against a live SystemLink Enterprise instance in
this repository's CI. Asset Dynamic LINQ property names for calibration status
follow the PascalCase style of the official `AssetIdentifier` example; if a
live server rejects that filter, that is a known integration risk documented
in `DEVLOG.md`.
