from __future__ import annotations

import pytest

from systemlink_mcp.backend import reset_backend


@pytest.fixture(autouse=True)
def _simulate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEMLINK_MCP_SIMULATE", "1")
    monkeypatch.delenv("SYSTEMLINK_MCP_ALLOW_WRITE", raising=False)
    monkeypatch.delenv("SYSTEMLINK_MCP_REQUIRE_REAL", raising=False)
    reset_backend()
    yield
    reset_backend()
