from __future__ import annotations

from ..domain.retrieval.text import clip_text


def _clip(value: object, limit: int) -> str:
    return clip_text(value, limit)
