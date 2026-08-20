from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Process env (Cursor mcp.json, the shell) wins over the file.
load_dotenv(_REPO_ROOT / ".env", override=False)


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Process configuration from environment variables."""

    simulate: bool
    require_real: bool
    allow_write: bool
    server_uri: str | None
    api_key: str | None
    username: str | None
    password: str | None
    workspace: str | None
    verify_tls: bool
    seed: int

    @property
    def has_explicit_server(self) -> bool:
        return bool(self.server_uri)


def load_settings() -> Settings:
    verify = os.getenv("SYSTEMLINK_VERIFY_TLS", "true").strip().lower()
    seed_raw = os.getenv("SYSTEMLINK_MCP_SEED", "42").strip()
    try:
        seed = int(seed_raw)
    except ValueError:
        seed = 42
    return Settings(
        simulate=_truthy("SYSTEMLINK_MCP_SIMULATE"),
        require_real=_truthy("SYSTEMLINK_MCP_REQUIRE_REAL"),
        allow_write=_truthy("SYSTEMLINK_MCP_ALLOW_WRITE"),
        server_uri=os.getenv("SYSTEMLINK_SERVER_URI") or None,
        api_key=os.getenv("SYSTEMLINK_API_KEY") or None,
        username=os.getenv("SYSTEMLINK_USERNAME") or None,
        password=os.getenv("SYSTEMLINK_PASSWORD") or None,
        workspace=os.getenv("SYSTEMLINK_WORKSPACE") or None,
        verify_tls=verify not in {"0", "false", "no", "off"},
        seed=seed,
    )
