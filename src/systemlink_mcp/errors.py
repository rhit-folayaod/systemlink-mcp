from __future__ import annotations

from typing import Any


class BackendError(Exception):
    """Structured failure from a backend operation. Tools convert this to an error dict."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "backend_error",
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": self.message,
            "code": self.code,
        }
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.details:
            payload["details"] = self.details
        return payload


def error_dict(message: str, *, code: str = "error") -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code}
