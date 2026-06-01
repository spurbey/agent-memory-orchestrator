from __future__ import annotations

import time
from typing import Any

from ....domain.retrieval.constants import ANSWER_SEED_KINDS
from ....domain.retrieval.policy import _apply_retrieval_policy
from ....domain.retrieval.policy import _expand_nodes
from ....domain.retrieval.policy import _filter_answer_grade_nodes
from ....domain.retrieval.policy import _kinds_for_intent
from ....domain.retrieval.policy import _rank_nodes
from ....domain.retrieval.policy import _sanitize_output_node
from ....domain.retrieval.policy import _seed_kinds_for_retrieval
from ....domain.retrieval.policy import _trim_weak_tail_matches
from ....infrastructure.kuzu import GraphStore
from ....llm.qwen import DeterministicPlanner, QwenPlanner, QwenUnavailable


def graph_search(
    *,
    store: GraphStore,
    planner: QwenPlanner,
    query: str,
    limit: int = 8,
    include_raw: bool = False,
    include_historical: bool = False,
) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    started = time.monotonic()
    timings: dict[str, int] = {}
    qwen_status: dict[str, Any] = {
        "planner_fallback": False,
        "compression_fallback": False,
        "planner_error": "",
        "compression_error": "",
    }
    try:
        plan_started = time.monotonic()
        plan = planner.plan_query(query)
        timings["planner_ms"] = _elapsed_ms(plan_started)
    except QwenUnavailable as exc:
        timings["planner_ms"] = _elapsed_ms(plan_started)
        qwen_status["planner_fallback"] = True
        qwen_status["planner_error"] = str(exc)
        plan = DeterministicPlanner().plan_query(query)
    plan = _apply_retrieval_policy(query=query, plan=plan, include_raw=include_raw)
    raw_requested = bool(plan.include_raw)
    kinds = _seed_kinds_for_retrieval(_kinds_for_intent(plan.intent), include_raw=raw_requested)
    search_started = time.monotonic()
    search_limit = max(limit * 12, 80)
    seed_nodes = store.search_nodes(query, limit=search_limit, kinds=kinds)
    expanded = _filter_answer_grade_nodes(_expand_nodes(seed_nodes, store), include_raw=raw_requested)
    candidates = _rank_nodes(query, expanded, include_historical=include_historical or plan.include_historical)
    if not candidates and not raw_requested:
        fallback_nodes = store.list_nodes(kinds=kinds or ANSWER_SEED_KINDS, limit=max(limit * 20, 120))
        fallback_filtered = _filter_answer_grade_nodes(fallback_nodes, include_raw=False)
        candidates = _rank_nodes(
            query,
            fallback_filtered,
            include_historical=include_historical or plan.include_historical,
            require_lexical=True,
        )
    candidates = _trim_weak_tail_matches(candidates)
    selected = [_sanitize_output_node(node) for node in candidates[: max(1, min(50, int(limit)))]]
    timings["retrieval_ms"] = _elapsed_ms(search_started)
    if selected:
        try:
            compression_started = time.monotonic()
            context = planner.compress_context(
                query=query,
                nodes=selected,
                include_raw=raw_requested,
            )
            timings["compression_ms"] = _elapsed_ms(compression_started)
        except QwenUnavailable as exc:
            timings["compression_ms"] = _elapsed_ms(compression_started)
            qwen_status["compression_fallback"] = True
            qwen_status["compression_error"] = str(exc)
            context = DeterministicPlanner().compress_context(
                query=query,
                nodes=selected,
                include_raw=raw_requested,
            )
    else:
        context = (
            "AMO GraphRAG context.\n"
            "No answer-grade graph memory matched this query. "
            "Raw evidence is available only through explicit raw-evidence retrieval."
        )
    return {
        "ok": True,
        "query": query,
        "plan": plan.as_dict(),
        "count": len(selected),
        "context": context,
        "nodes": selected,
        "raw_included": raw_requested,
        "qwen": qwen_status,
        "timing": {**timings, "total_ms": _elapsed_ms(started)},
    }


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
