from __future__ import annotations

import json
from typing import Any
from urllib.request import urlopen

from helixdb import Client

from .config import HelixHarnessConfig


class HelixHarnessClient:
    """Small synchronous boundary around the Helix dynamic-query client."""

    def __init__(self, config: HelixHarnessConfig | None = None) -> None:
        self.config = config or HelixHarnessConfig.from_env()
        self._client = Client(self.config.url)

    def send(self, request: Any) -> dict[str, Any]:
        result = self._client.query().dynamic(request).send()
        return result if isinstance(result, dict) else {}

    def healthy(self) -> bool:
        try:
            with urlopen(f"{self.config.url}/health", timeout=5) as response:  # nosec B310: local configured endpoint
                payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("healthy"))
        except Exception:
            return False


__all__ = ["HelixHarnessClient"]
