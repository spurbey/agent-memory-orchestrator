from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class RetrievalStorePort(Protocol):
    """Searchable retrieval projection boundary."""

    def upsert_documents(self, documents: Iterable[Mapping[str, Any]]) -> int:
        """Persist retrieval documents and return the number processed."""

    def search(self, query: str, *, limit: int = 10, repo_id: str = "") -> list[Mapping[str, Any]]:
        """Return ranked retrieval candidates for a query."""
