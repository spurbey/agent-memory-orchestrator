from __future__ import annotations

from typing import Any, Iterable

from ...llm.embeddings import embed_text


class StrictTextEmbedder:
    """Production text embedder. It fails loudly instead of producing fake vectors."""

    def __init__(self, model_name: str, *, dims: int = 256) -> None:
        self.model_name = model_name
        self._hash_dims = 0
        if model_name.strip().lower() in {"hash", "hash-fallback", "deterministic", "local-hash"}:
            self._model = None
            self._cache: dict[str, list[float]] = {}
            self.dims = max(1, int(dims))
            self._hash_dims = self.dims
            return
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"text_embedding_runtime_unavailable:{exc}") from exc
        try:
            self._model = SentenceTransformer(model_name, local_files_only=True)
        except TypeError:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"text_embedding_model_unavailable:{model_name}:{exc}") from exc
        self._cache: dict[str, list[float]] = {}
        self.dims = _model_embedding_dimension(self._model)

    def embed(self, text: str) -> list[float]:
        text = text or ""
        if text in self._cache:
            return self._cache[text]
        if self._hash_dims:
            result = embed_text(text, self._hash_dims)
            self._cache[text] = result
            return result
        vector = self._model.encode(text, normalize_embeddings=True)
        result = [float(x) for x in vector.tolist()]
        self._cache[text] = result
        return result

    def embed_many(self, texts: Iterable[str], *, batch_size: int = 16) -> None:
        missing = [text or "" for text in texts if (text or "") not in self._cache]
        if not missing:
            return
        unique = list(dict.fromkeys(missing))
        if self._hash_dims:
            for text in unique:
                self._cache[text] = embed_text(text, self._hash_dims)
            return
        vectors = self._model.encode(unique, batch_size=batch_size, normalize_embeddings=True)
        for text, vector in zip(unique, vectors, strict=True):
            self._cache[text] = [float(x) for x in vector.tolist()]


def _model_embedding_dimension(model: Any) -> int:
    current = getattr(model, "get_embedding_dimension", None)
    if callable(current):
        return int(current() or 0)
    legacy = getattr(model, "get_sentence_embedding_dimension", None)
    if callable(legacy):
        return int(legacy() or 0)
    return 0

