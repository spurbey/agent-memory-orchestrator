from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class SymbolRecord:
    """Domain contract for symbol rows produced by the production code graph."""

    symbol_id: str
    symbol_key: str
    qualified_name: str
    symbol_kind: str = ""
    first_packet_id: str = ""
    latest_packet_id: str = ""
    first_commit_sha: str = ""
    latest_commit_sha: str = ""
    version_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "symbol_key": self.symbol_key,
            "qualified_name": self.qualified_name,
            "symbol_kind": self.symbol_kind,
            "first_packet_id": self.first_packet_id,
            "latest_packet_id": self.latest_packet_id,
            "first_commit_sha": self.first_commit_sha,
            "latest_commit_sha": self.latest_commit_sha,
            "version_count": self.version_count,
            **self.metadata,
        }


def symbol_key(path: str, qualified_name: str) -> str:
    return f"{path}::{qualified_name}"


__all__ = ["SymbolRecord", "symbol_key"]
