from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..config import Settings


class DaemonUnavailable(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class DaemonClient:
    base_url: str
    timeout_seconds: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings, *, timeout_seconds: float = 5.0) -> "DaemonClient":
        return cls(f"http://{settings.mcp_host}:{settings.mcp_port}", timeout_seconds=timeout_seconds)

    def health(self) -> dict[str, Any]:
        return self.get("/health")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        suffix = path if path.startswith("/") else f"/{path}"
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None and v != ""})
        url = f"{self.base_url.rstrip('/')}{suffix}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, method="GET")
        return self._request(request)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        suffix = path if path.startswith("/") else f"/{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{suffix}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._request(request)

    def _request(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DaemonUnavailable(f"daemon_unavailable:{exc}") from exc
        if not isinstance(payload, dict):
            raise DaemonUnavailable("daemon_response_must_be_object")
        return payload
