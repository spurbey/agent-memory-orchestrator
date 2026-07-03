from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..projection import HarnessProjectionDocument
from .embeddings import HASH_COSINE_METHOD
from .embeddings import hash_embed_text
from .models import HashVectorOptions


@dataclass(slots=True, frozen=True)
class EmbeddingRecord:
    doc_id: str
    content_hash: str
    embedding_method: str
    status: str
    vector: tuple[float, ...] = ()

    def as_dict(self, *, include_vector: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "doc_id": self.doc_id,
            "content_hash": self.content_hash,
            "embedding_method": self.embedding_method,
            "status": self.status,
        }
        if include_vector:
            out["vector"] = list(self.vector)
        return out


@dataclass(slots=True, frozen=True)
class EmbeddingIndexManifest:
    embedding_method: str
    projection_id: str
    doc_count: int
    embedded_count: int
    reused_count: int
    tombstoned_count: int
    records: tuple[EmbeddingRecord, ...]

    def as_dict(self, *, include_vectors: bool = False) -> dict[str, Any]:
        return {
            "embedding_method": self.embedding_method,
            "projection_id": self.projection_id,
            "doc_count": self.doc_count,
            "embedded_count": self.embedded_count,
            "reused_count": self.reused_count,
            "tombstoned_count": self.tombstoned_count,
            "records": [record.as_dict(include_vector=include_vectors) for record in self.records],
        }


def build_hash_embedding_manifest(
    *,
    projection_id: str,
    documents: tuple[HarnessProjectionDocument, ...],
    previous: EmbeddingIndexManifest | None = None,
    options: HashVectorOptions = HashVectorOptions(),
) -> EmbeddingIndexManifest:
    previous_by_doc = {record.doc_id: record for record in previous.records} if previous is not None else {}
    active_doc_ids = {document.doc_id for document in documents}
    records: list[EmbeddingRecord] = []
    embedded_count = 0
    reused_count = 0
    tombstoned_count = 0
    for document in sorted(documents, key=lambda item: item.doc_id):
        previous_record = previous_by_doc.get(document.doc_id)
        if previous_record is not None and previous_record.content_hash == document.content_hash:
            records.append(previous_record)
            reused_count += 1
            continue
        records.append(
            EmbeddingRecord(
                doc_id=document.doc_id,
                content_hash=document.content_hash,
                embedding_method=HASH_COSINE_METHOD,
                status="embedded",
                vector=hash_embed_text(_embedding_text(document), options=options),
            )
        )
        embedded_count += 1
    for record in previous_by_doc.values():
        if record.doc_id in active_doc_ids:
            continue
        records.append(
            EmbeddingRecord(
                doc_id=record.doc_id,
                content_hash=record.content_hash,
                embedding_method=record.embedding_method,
                status="tombstoned",
            )
        )
        tombstoned_count += 1
    return EmbeddingIndexManifest(
        embedding_method=HASH_COSINE_METHOD,
        projection_id=projection_id,
        doc_count=len(documents),
        embedded_count=embedded_count,
        reused_count=reused_count,
        tombstoned_count=tombstoned_count,
        records=tuple(sorted(records, key=lambda item: (item.status == "tombstoned", item.doc_id))),
    )


def _embedding_text(document: HarnessProjectionDocument) -> str:
    metadata_text = " ".join(str(value) for value in document.metadata.values() if value)
    return "\n".join((document.title, document.text, metadata_text))


__all__ = ["EmbeddingIndexManifest", "EmbeddingRecord", "build_hash_embedding_manifest"]
