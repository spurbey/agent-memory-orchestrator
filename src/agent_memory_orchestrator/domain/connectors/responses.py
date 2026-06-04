"""Source-neutral connector response contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(slots=True, frozen=True)
class ConnectorResponse:
    ok: bool
    messages: Sequence[str] = field(default_factory=tuple)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["ConnectorResponse"]
