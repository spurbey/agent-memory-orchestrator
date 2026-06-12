from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from .models import HashVectorOptions
from .tokenization import tokenize_text


HASH_COSINE_METHOD = "hash_token_char_cosine_v1"
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def hash_embed_text(text: str, *, options: HashVectorOptions = HashVectorOptions()) -> tuple[float, ...]:
    dimensions = max(16, int(options.dimensions or 0))
    values = [0.0] * dimensions
    for feature, weight in hash_embedding_features(text, options=options).items():
        index, sign = _feature_slot(feature, dimensions)
        values[index] += weight * sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return tuple(values)
    return tuple(value / norm for value in values)


def hash_embedding_features(text: str, *, options: HashVectorOptions = HashVectorOptions()) -> Counter[str]:
    features: Counter[str] = Counter()
    for token in tokenize_text(text):
        features[f"tok:{token}"] += options.token_weight
        for ngram in _char_ngrams(token, options.char_ngram_size):
            features[f"chr:{ngram}"] += options.char_ngram_weight
    for alias in _identifier_aliases(text):
        features[f"tok:{alias}"] += options.token_weight * 0.82
        for ngram in _char_ngrams(alias, options.char_ngram_size):
            features[f"chr:{ngram}"] += options.char_ngram_weight * 0.7
    return features


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _char_ngrams(token: str, size: int) -> tuple[str, ...]:
    safe = str(token or "").strip().lower()
    n = max(2, int(size or 0))
    if len(safe) < n:
        return ()
    return tuple(safe[idx : idx + n] for idx in range(0, len(safe) - n + 1))


def _identifier_aliases(text: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for raw in _IDENTIFIER_RE.findall(str(text or "")):
        parts = [part.lower() for part in _split_identifier(raw) if part]
        if len(parts) < 2:
            continue
        aliases.append("".join(parts[:2]))
        aliases.append("".join(parts))
    return tuple(dict.fromkeys(alias for alias in aliases if len(alias) >= 4))


def _split_identifier(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    for chunk in str(value or "").replace("_", " ").split():
        parts.extend(part for part in _CAMEL_BOUNDARY_RE.sub(" ", chunk).split() if part)
    return tuple(parts)


def _feature_slot(feature: str, dimensions: int) -> tuple[int, int]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dimensions, 1 if (value >> 63) == 0 else -1


__all__ = [
    "HASH_COSINE_METHOD",
    "cosine_similarity",
    "hash_embed_text",
    "hash_embedding_features",
]
