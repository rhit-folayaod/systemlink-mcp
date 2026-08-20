from __future__ import annotations

import pytest

import server
from systemlink_mcp.backend import create_backend, reset_backend
from systemlink_mcp.config import load_settings
from systemlink_mcp.errors import BackendError


def test_probe_refuses_when_simulate_is_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEMLINK_MCP_SIMULATE", "1")
    monkeypatch.delenv("SYSTEMLINK_MCP_REQUIRE_REAL", raising=False)
    with pytest.raises(SystemExit) as exc:
        server.main(["--probe"])
    assert exc.value.code == 1


def test_require_real_blocks_simulate_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEMLINK_MCP_SIMULATE", "1")
    monkeypatch.setenv("SYSTEMLINK_MCP_REQUIRE_REAL", "1")
    reset_backend()
    with pytest.raises(BackendError) as exc:
        create_backend(load_settings())
    assert exc.value.code == "simulate_forced"
