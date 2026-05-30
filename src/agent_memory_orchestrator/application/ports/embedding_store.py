from __future__ import annotations

from typing import Iterable, Protocol, Sequence, runtime_checkable


@runtime_checkable
class EmbeddingStorePort(Protocol):
    """Vector persistence boundary for retrieval services."""

    def upsert_embedding(self, key: str, vector: Sequence[float], *, scope: str = "") -> None:
        """Persist one vector under a stable key."""

    def search(self, vector: Sequence[float], *, limit: int = 10, scope: str = "") -> Iterable[tuple[str, float]]:
        """Return nearest vector ids with similarity scores."""
