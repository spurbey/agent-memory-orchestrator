from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "before",
        "be",
        "by",
        "can",
        "could",
        "determine",
        "edit",
        "edits",
        "file",
        "files",
        "for",
        "from",
        "higher",
        "how",
        "i",
        "identify",
        "in",
        "is",
        "it",
        "need",
        "needs",
        "of",
        "on",
        "or",
        "relevant",
        "harness",
        "semantic",
        "should",
        "than",
        "the",
        "this",
        "to",
        "use",
        "what",
        "when",
        "whether",
        "why",
        "would",
        "with",
    }
)


def tokenize_text(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(str(text or "")):
        for part in _split_identifier(raw):
            token = part.lower()
            if len(token) < 2 or token in STOPWORDS:
                continue
            terms.append(token)
    return tuple(terms)


def _split_identifier(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    for chunk in str(value or "").replace("_", " ").split():
        parts.extend(part for part in _CAMEL_BOUNDARY_RE.sub(" ", chunk).split() if part)
    return tuple(parts)


__all__ = ["STOPWORDS", "tokenize_text"]
