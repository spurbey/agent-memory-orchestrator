from __future__ import annotations

from typing import Any, Iterable

from ....infrastructure.llm.text_embedder import _model_embedding_dimension as _infra_model_embedding_dimension
from .constants import DEFAULT_CODE_EMBEDDING_MODEL


def _model_embedding_dimension(model: Any) -> int:
    return _infra_model_embedding_dimension(model)


class CodeBertEmbedder:
    """Strict local CodeBERT embedder for code-node search."""

    def __init__(self, model_name: str = DEFAULT_CODE_EMBEDDING_MODEL) -> None:
        self.model_name = model_name
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"code_embedding_runtime_unavailable:{exc}") from exc
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
            self._model = AutoModel.from_pretrained(model_name, local_files_only=True)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"code_embedding_model_unavailable:{model_name}:{exc}") from exc
        self._torch = torch
        self._model.eval()
        self.dims = int(getattr(self._model.config, "hidden_size", 0) or 0)
        self._cache: dict[str, list[float]] = {}

    def embed(self, code: str) -> list[float]:
        code = code or ""
        if code in self._cache:
            return self._cache[code]
        self.embed_many([code], batch_size=1)
        return self._cache[code]

    def embed_many(self, snippets: Iterable[str], *, batch_size: int = 8) -> None:
        missing = [snippet or "" for snippet in snippets if (snippet or "") not in self._cache]
        if not missing:
            return
        unique = list(dict.fromkeys(missing))
        for offset in range(0, len(unique), max(1, batch_size)):
            batch = unique[offset : offset + batch_size]
            inputs = self._tokenizer(
                batch,
                max_length=384,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            with self._torch.no_grad():
                output = self._model(**inputs)
                mask = inputs["attention_mask"].unsqueeze(-1).expand(output.last_hidden_state.size()).float()
                pooled = (output.last_hidden_state * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                normed = self._torch.nn.functional.normalize(pooled, p=2, dim=1)
            for text, vector in zip(batch, normed, strict=True):
                self._cache[text] = [float(x) for x in vector.tolist()]


__all__ = ["CodeBertEmbedder", "_model_embedding_dimension"]
