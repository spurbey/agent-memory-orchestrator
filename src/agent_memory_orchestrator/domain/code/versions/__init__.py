from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ....reasoning_graph.code_versioning import CodeVersionPlan
from ....reasoning_graph.code_versioning import CodeVersionRelation
from ....reasoning_graph.code_versioning import resolve_code_node_version


@dataclass(slots=True, frozen=True)
class CodeVersionRecord:
    """Domain contract for code-version rows produced by symbol versioning."""

    version_id: str
    symbol_id: str
    code_node_id: str
    packet_id: str = ""
    commit_sha: str = ""
    path: str = ""
    qualified_name: str = ""
    symbol_kind: str = ""
    version_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "symbol_id": self.symbol_id,
            "code_node_id": self.code_node_id,
            "packet_id": self.packet_id,
            "commit_sha": self.commit_sha,
            "path": self.path,
            "qualified_name": self.qualified_name,
            "symbol_kind": self.symbol_kind,
            "version_index": self.version_index,
            **self.metadata,
        }


__all__ = [
    "CodeVersionPlan",
    "CodeVersionRecord",
    "CodeVersionRelation",
    "resolve_code_node_version",
]
