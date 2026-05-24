from __future__ import annotations

from typing import Any


def build_decision_review_candidates(*, session_nodes: list[dict[str, Any]], central_nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    del central_nodes
    return {
        "candidates": [],
        "metrics": {
            "decision_candidate_count": 0,
            "review_candidate_count": 0,
            "deferred_session_decision_count": sum(1 for node in session_nodes if _is_decision_like(node)),
            "note": "Decision/problem semantic matching is intentionally dry-run/deferred until exact atom merge is proven.",
        },
    }


def _is_decision_like(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or node.get("node_kind") or "").lower()
    return kind in {"reasoningnode", "decision", "problem"}
