from __future__ import annotations


def _clip(value: object, limit: int) -> str:
    text = str(value or '').strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)].rstrip() + ' ...<clipped>'
