from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class HelixHarnessConfig:
    url: str = "http://127.0.0.1:6969"
    batch_size: int = 250

    @classmethod
    def from_env(cls) -> "HelixHarnessConfig":
        url = os.environ.get("AMO_HELIX_URL", "http://127.0.0.1:6969").strip()
        raw_batch_size = os.environ.get("AMO_HELIX_BATCH_SIZE", "250").strip()
        try:
            batch_size = max(1, int(raw_batch_size))
        except ValueError:
            batch_size = 250
        return cls(url=url.rstrip("/"), batch_size=batch_size)


__all__ = ["HelixHarnessConfig"]
