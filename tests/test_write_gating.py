from __future__ import annotations

import server
from systemlink_mcp.backend import reset_backend
from systemlink_mcp.models import NotebookExecutionSummary, UpdateResultSummary


def test_mutating_tools_refuse_when_write_env_unset() -> None:
    refused = server.update_result_properties("res-fail-dut1001", keywords=["triaged"])
    assert refused["ok"] is False
    assert refused["code"] == "writes_disabled"

    refused_nb = server.execute_notebook("nb-1", "ws-1", {"x": 1})
    assert refused_nb["ok"] is False
    assert refused_nb["code"] == "writes_disabled"


def test_mutating_tools_run_when_write_env_set(monkeypatch) -> None:
    monkeypatch.setenv("SYSTEMLINK_MCP_ALLOW_WRITE", "1")
    reset_backend()

    updated = server.update_result_properties(
        "res-fail-dut1001",
        keywords=["triaged"],
        properties={"owner": "night-shift"},
    )
    UpdateResultSummary.model_validate(updated)
    assert updated["keywords"] == ["triaged"]
    assert updated["properties"]["owner"] == "night-shift"

    started = server.execute_notebook("nb-yield-report", "ws-lab", {"part": "PN-5164-B"})
    NotebookExecutionSummary.model_validate(started)
    assert started["status"] == "QUEUED"
    assert started["execution_id"]
