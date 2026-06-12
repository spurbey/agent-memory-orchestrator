from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class HarnessProjectionDocument:
    doc_id: str
    repo_id: str
    source_node_id: str
    source_kind: str
    doc_type: str
    title: str
    text: str
    metadata: dict[str, Any]

    @property
    def content_hash(self) -> str:
        stable = "\n".join([self.repo_id, self.source_node_id, self.doc_type, self.title, self.text])
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "repo_id": self.repo_id,
            "source_node_id": self.source_node_id,
            "source_kind": self.source_kind,
            "doc_type": self.doc_type,
            "title": self.title,
            "text": self.text,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }


__all__ = ["HarnessProjectionDocument"]
