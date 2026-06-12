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


@dataclass(slots=True, frozen=True)
class HarnessProjectionSet:
    repo_id: str
    projection_id: str
    projection_version: str
    graph_snapshot_id: str
    graph_schema_version: str
    documents: tuple[HarnessProjectionDocument, ...]

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def document_ids_hash(self) -> str:
        stable = "\n".join(sorted(document.doc_id for document in self.documents))
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def as_dict(self, *, include_documents: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "repo_id": self.repo_id,
            "projection_id": self.projection_id,
            "projection_version": self.projection_version,
            "graph_snapshot_id": self.graph_snapshot_id,
            "graph_schema_version": self.graph_schema_version,
            "document_count": self.document_count,
            "document_ids_hash": self.document_ids_hash,
        }
        if include_documents:
            out["documents"] = [document.as_dict() for document in self.documents]
        return out


__all__ = ["HarnessProjectionDocument", "HarnessProjectionSet"]
