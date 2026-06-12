from __future__ import annotations

from dataclasses import dataclass

from ..models import HarnessNode


@dataclass(slots=True, frozen=True)
class DocSemanticArtifact:
    node: HarnessNode
    text: str
    source_file_id: str


__all__ = ["DocSemanticArtifact"]
