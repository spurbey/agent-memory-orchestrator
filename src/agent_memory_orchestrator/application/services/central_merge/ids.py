from __future__ import annotations

import hashlib


def _edge_id(kind: str, source_id: str, target_id: str, graph_commit_id: str) -> str:
    return f"edge:{hashlib.sha256(f'{kind}|{source_id}|{target_id}|{graph_commit_id}'.encode('utf-8')).hexdigest()[:32]}"


def _idempotency_key(item_type: str, item_id: str, graph_commit_id: str) -> str:
    return hashlib.sha256(f"{item_type}|{item_id}|{graph_commit_id}".encode("utf-8")).hexdigest()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


__all__ = ["_dedupe", "_edge_id", "_idempotency_key"]
