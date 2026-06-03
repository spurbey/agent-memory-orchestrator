from __future__ import annotations

import hashlib
import math
from functools import lru_cache


@lru_cache(maxsize=2)
def _load_sentence_transformer(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception:
        return None
    try:
        return SentenceTransformer(model_name, local_files_only=True)
    except TypeError:
        return None
    except Exception:
        return None


def embed_text_with_model(text: str, dims: int, model_name: str = "BAAI/bge-m3") -> tuple[list[float], str]:
    if model_name.strip().lower() in {"hash", "hash-fallback", "deterministic", "local-hash"}:
        return embed_text(text, dims), "hash-fallback"
    model = _load_sentence_transformer(model_name)
    if model is None:
        return embed_text(text, dims), "hash-fallback"

    vector = model.encode(text, normalize_embeddings=True)
    values = [float(v) for v in vector.tolist()]
    return values, model_name


def embed_text(text: str, dims: int) -> list[float]:
    if dims <= 0:
        raise ValueError("dims must be > 0")

    vector = [0.0] * dims
    tokens = [tok for tok in text.lower().split() if tok]
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dims
        sign = -1.0 if digest[4] % 2 else 1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vector dimensions must match")
    numerator = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return numerator / (norm_a * norm_b)
