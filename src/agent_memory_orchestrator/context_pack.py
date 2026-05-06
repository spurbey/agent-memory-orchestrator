from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DURABLE_TYPE_ORDER = {
    "decision": 0,
    "fix": 1,
    "bug": 2,
    "blocker": 2,
    "validation": 3,
    "summary": 4,
    "file_change": 5,
    "observation": 6,
}


MIN_CONTEXT_PACK_SCORE_BY_TYPE = {
    "decision": 0.34,
    "fix": 0.34,
    "bug": 0.38,
    "blocker": 0.38,
    "validation": 0.34,
    "summary": 0.30,
    "file_change": 0.42,
    "reference": 0.42,
    "observation": 0.55,
}


@dataclass(slots=True, frozen=True)
class ContextPack:
    text: str
    payload: dict[str, Any]


def build_context_pack_payload(
    *,
    query: str,
    results: list[dict],
    budget_tokens: int,
    retrieval_run_id: str | None = None,
    include_historical: bool = False,
) -> ContextPack:
    budget = max(1, int(budget_tokens))
    ordered = sorted(results, key=_pack_sort_key)
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    used = _estimate_tokens(_pack_header(query))

    for item in ordered:
        exclusion = _pre_budget_exclusion(item, include_historical)
        if exclusion:
            excluded.append(_excluded(item, exclusion))
            continue
        rendered = _render_item(len(included) + 1, item)
        item_tokens = _estimate_tokens(rendered)
        if used + item_tokens > budget:
            excluded.append(_excluded(item, "budget"))
            continue
        included.append(_included(item, rendered, item_tokens))
        used += item_tokens

    text = _render_pack_text(query, included)
    payload = {
        "query": query,
        "budget_tokens": budget,
        "estimated_tokens": _estimate_tokens(text),
        "retrieval_run_id": retrieval_run_id,
        "items": [{key: value for key, value in item.items() if key != "_rendered"} for item in included],
        "excluded": excluded,
    }
    return ContextPack(text=text, payload=payload)


def estimate_tokens(text: str) -> int:
    return _estimate_tokens(text)


def _pack_sort_key(item: dict) -> tuple[float, float, float]:
    type_order = DURABLE_TYPE_ORDER.get(str(item.get("memory_type") or ""), 7)
    score = float(item.get("score") or 0.0)
    confidence = float(item.get("confidence") or 0.0)
    return (-score, float(type_order), -confidence)


def _pre_budget_exclusion(item: dict, include_historical: bool) -> str:
    if item.get("status") != "active" and not include_historical:
        return "superseded"
    summary = str(item.get("summary") or "").lower()
    if any(marker in summary for marker in ("context from my ide setup", "open tabs:", "active file:")):
        return "ide_context_noise"
    if _looks_like_raw_tool_json(summary):
        return "raw_tool_json_noise"
    memory_type = str(item.get("memory_type") or "")
    score = float(item.get("score") or 0.0)
    if memory_type == "observation":
        confidence = float(item.get("confidence") or 0.0)
        policy = item.get("ranking_policy") if isinstance(item.get("ranking_policy"), dict) else {}
        exact = float(policy.get("exact_boost") or 0.0)
        if score < 0.45 or confidence < 0.5 or exact <= 0.0:
            return "observation_noise"
    if score < MIN_CONTEXT_PACK_SCORE_BY_TYPE.get(memory_type, 0.45):
        return "low_relevance_score"
    return ""


def _looks_like_raw_tool_json(summary: str) -> bool:
    return (
        '"call_id"' in summary
        and '"invocation"' in summary
        and ('"result"' in summary or '"duration"' in summary)
    )


def _included(item: dict, rendered: str, tokens: int) -> dict[str, Any]:
    policy = item.get("ranking_policy") if isinstance(item.get("ranking_policy"), dict) else {}
    return {
        "memory_id": item.get("memory_id"),
        "session_id": item.get("session_id"),
        "memory_type": item.get("memory_type"),
        "status": item.get("status"),
        "summary": item.get("summary"),
        "source_event_id": item.get("source_event_id"),
        "source_chunk_id": item.get("source_chunk_id"),
        "score": item.get("score"),
        "include_reason": _include_reason(item),
        "ranking_policy": policy,
        "tokens": tokens,
        "_rendered": rendered,
    }


def _excluded(item: dict, reason: str) -> dict[str, Any]:
    return {
        "memory_id": item.get("memory_id"),
        "memory_type": item.get("memory_type"),
        "status": item.get("status"),
        "score": item.get("score"),
        "reason": reason,
    }


def _include_reason(item: dict) -> str:
    memory_type = str(item.get("memory_type") or "memory")
    policy = item.get("ranking_policy") if isinstance(item.get("ranking_policy"), dict) else {}
    matched = policy.get("matched_terms") or []
    parts = [f"{memory_type} memory"]
    if matched:
        parts.append("matched " + ", ".join(str(term) for term in matched[:5]))
    if float(policy.get("exact_boost") or 0.0) > 0:
        parts.append("exact/entity boost")
    if float(item.get("confidence") or 0.0) >= 0.8:
        parts.append("high confidence")
    return "; ".join(parts)


def _render_pack_text(query: str, included: list[dict[str, Any]]) -> str:
    lines = [_pack_header(query)]
    if not included:
        lines.append("No relevant local memories fit the current context policy.")
        return "\n".join(lines)
    for item in included:
        lines.append(item["_rendered"])
    return "\n".join(lines)


def _pack_header(query: str) -> str:
    return (
        "AMO local memory context.\n"
        "Use only if relevant. Cite memory_id when relying on it.\n"
        f"Query: {query}"
    )


def _render_item(index: int, item: dict) -> str:
    summary = " ".join(str(item.get("summary") or "").split())
    if len(summary) > 520:
        summary = summary[:517] + "..."
    policy = item.get("ranking_policy") if isinstance(item.get("ranking_policy"), dict) else {}
    matched = ", ".join(str(term) for term in (policy.get("matched_terms") or [])[:6])
    return (
        f"\n{index}. [{item.get('memory_type')}] memory_id={item.get('memory_id')} "
        f"status={item.get('status')} score={item.get('score')} session={item.get('session_id')}\n"
        f"   Summary: {summary}\n"
        f"   Evidence: event={item.get('source_event_id')} chunk={item.get('source_chunk_id')}\n"
        f"   Why included: {_include_reason(item)}"
        + (f"\n   Matched terms: {matched}" if matched else "")
    )


def _estimate_tokens(text: str) -> int:
    words = len(str(text or "").split())
    return max(1, int(words / 0.75))
