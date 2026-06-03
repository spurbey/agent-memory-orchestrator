from __future__ import annotations

import ast
import json
import re
from typing import Any, Protocol

from .constants import ANSWER_SEED_KINDS
from .constants import EVIDENCE_ONLY_KINDS
from .constants import RETRIEVAL_STOPWORDS
from .constants import SUPPORT_ONLY_KINDS


class NeighborGraphStore(Protocol):
    def neighbors(self, node_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return graph neighbors for retrieval expansion."""


def _kinds_for_intent(intent: str) -> list[str] | None:
    if intent == "decision_lookup":
        return ["KnowledgeVersion", "ReasoningNode", "DecisionUnit", "Decision", "Fix", "WorkChange", "Prompt", "Response", "ToolResult"]
    if intent == "work_history":
        return ["KnowledgeVersion", "ReasoningNode", "WorkChange", "GitCommit", "Commit", "File", "Decision", "Fix", "ToolResult"]
    if intent == "bug_fix_trace":
        return ["Bug", "Fix", "TestRun", "WorkChange", "ReasoningNode", "GitCommit", "Commit"]
    if intent == "historical_versions":
        return ["KnowledgeVersion", "Decision", "Fix", "WorkChange", "ReasoningNode", "GitCommit", "Commit"]
    return None


def _seed_kinds_for_retrieval(kinds: list[str] | None, *, include_raw: bool) -> list[str] | None:
    if include_raw:
        return kinds
    allowed = set(ANSWER_SEED_KINDS)
    if kinds is None:
        return ANSWER_SEED_KINDS
    filtered = [kind for kind in kinds if kind in allowed]
    return filtered or ANSWER_SEED_KINDS


def _expand_nodes(seed_nodes: list[dict[str, Any]], store: NeighborGraphStore) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for node in seed_nodes:
        seen[str(node["id"])] = node
        for neighbor in store.neighbors(str(node["id"]), limit=4):
            seen.setdefault(str(neighbor["id"]), neighbor)
    return list(seen.values())


def _filter_answer_grade_nodes(nodes: list[dict[str, Any]], *, include_raw: bool) -> list[dict[str, Any]]:
    if include_raw:
        return nodes
    filtered: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("kind") in EVIDENCE_ONLY_KINDS:
            continue
        if node.get("kind") in SUPPORT_ONLY_KINDS:
            continue
        if not _is_answer_quality_node(node):
            continue
        filtered.append(node)
    return filtered


def _apply_retrieval_policy(*, query: str, plan: Any, include_raw: bool) -> Any:
    raw_allowed = bool(include_raw or _is_explicit_raw_request(query))
    include_raw_final = bool(plan.include_raw and raw_allowed) or bool(include_raw)
    intent = plan.intent
    if intent == "raw_evidence" and not include_raw_final:
        intent = "general"
    return plan.__class__(
        intent=intent,
        entities=plan.entities,
        include_raw=include_raw_final,
        include_historical=plan.include_historical,
    )


def _is_explicit_raw_request(query: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(query or "").lower()).strip()
    raw_phrases = (
        "include raw",
        "show raw",
        "raw evidence",
        "raw payload",
        "raw transcript",
        "raw log",
        "raw logs",
        "raw jsonl",
        "raw record",
        "raw records",
        "raw event",
        "raw events",
        "evidence payload",
        "evidence ref",
        "evidence refs",
        "evidence record",
        "evidence records",
        "original payload",
        "verbatim evidence",
    )
    return any(phrase in lowered for phrase in raw_phrases)


def _sanitize_output_node(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata")
    if not isinstance(metadata, dict):
        return node
    cleaned = dict(node)
    cleaned["metadata"] = _sanitize_metadata(metadata)
    return cleaned


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(metadata)
    for key in ("goal", "latest_decision", "next_step"):
        if key in cleaned:
            cleaned[key] = _scalar_metadata_value(cleaned[key])
    for key in ("changed_files", "tests", "blockers", "evidence_ids"):
        if key in cleaned:
            cleaned[key] = _list_metadata_value(cleaned[key])
    return cleaned


def _scalar_metadata_value(value: Any) -> str:
    parsed = _parse_literal_list(value)
    if parsed is not None:
        value = parsed
    if isinstance(value, list):
        rows = [_scalar_metadata_value(item) for item in value]
        return " ".join(row for row in rows if row)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


def _list_metadata_value(value: Any) -> list[str]:
    parsed = _parse_literal_list(value)
    if parsed is not None:
        value = parsed
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _scalar_metadata_value(item)
        if text:
            rows.append(text)
    return rows


def _parse_literal_list(value: Any) -> list[Any] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _is_answer_quality_node(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or "")
    if kind in {"WorkChange", "Decision", "Fix", "Bug", "Blocker", "TestRun"}:
        return _is_clean_answer_summary(str(node.get("summary") or ""), node.get("metadata"))
    return True


def _rank_nodes(
    query: str,
    nodes: list[dict[str, Any]],
    *,
    include_historical: bool,
    require_lexical: bool = False,
) -> list[dict[str, Any]]:
    terms = _retrieval_terms(query)
    query_term_set = set(terms)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        if not include_historical and node.get("status") in {"superseded", "abandoned"}:
            continue
        text = f"{node.get('kind')} {node.get('label')} {node.get('summary')} {json.dumps(node.get('metadata', {}), sort_keys=True)}".lower()
        node_terms = set(_retrieval_terms(text))
        lexical = float(len(query_term_set & node_terms))
        substring = sum(0.25 for term in query_term_set - node_terms if term in text)
        lexical += substring
        graph_score = float(node.get("graph_score") or 0.0)
        if terms and lexical <= 0 and graph_score <= 0:
            continue
        if require_lexical and terms and lexical <= 0:
            continue
        status = str(node.get("status") or "")
        if status == "committed":
            status_bonus = 2.0
        elif status == "active":
            status_bonus = 1.0
        elif status == "draft":
            status_bonus = 0.25
        else:
            status_bonus = 0.0
        evidence_bonus = 0.5 if node.get("evidence_id") else 0.0
        ranked.append((lexical + status_bonus + evidence_bonus + graph_score, node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{**node, "score": round(score, 6)} for score, node in ranked]


def _retrieval_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_.-]+", str(text or "").lower()):
        if len(token) <= 2:
            continue
        if token in RETRIEVAL_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        terms.append(token)
    return terms


def _trim_weak_tail_matches(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not nodes:
        return nodes
    top_score = float(nodes[0].get("score") or 0.0)
    if top_score < 4.0:
        return nodes
    floor = max(2.0, top_score - 3.0)
    trimmed = [node for node in nodes if float(node.get("score") or 0.0) >= floor]
    return trimmed or nodes[:1]


def _is_clean_answer_summary(summary: str, metadata: object = None) -> bool:
    text = summary.strip()
    lowered = text.lower()
    generic_summaries = {
        "update files in the session",
        "git commit operation executed",
        "git commit operation executed.",
    }
    if lowered in generic_summaries:
        return False
    if len(text) < 16:
        return False
    if re.match(r"^(from\s+[\w.]+\s+import\b|import\s+[\w.]+\b|class\s+\w+\b|def\s+\w+\b)", lowered):
        return False
    if re.search(r"\|\s*(from\s+[\w.]+\s+import\b|import\s+[\w.]+\b|class\s+\w+\b|def\s+\w+\b)", lowered):
        return False
    noisy_prefixes = (
        '"continue":',
        "{",
        "[",
        "from __future__",
        "import ",
        "class ",
        "def ",
        "raise ",
        "assert ",
        "return ",
        "all checks passed!",
    )
    noisy_terms = (
        "manualsmoke",
        "captureonly",
        "hook_event_name",
        "status_porcelain",
        "after_preview",
        "raw_",
        "traceback",
        "content-length",
    )
    if lowered.startswith(noisy_prefixes):
        return False
    if any(term in lowered for term in noisy_terms):
        return False
    if text.count(" | ") >= 6 and (
        _code_token_ratio(text) > 0.05
        or "return " in lowered
        or "_write_" in lowered
        or "_read_" in lowered
    ):
        return False
    if len(text) > 600 and text.count(" | ") >= 6:
        return False
    if len(text) > 600 and _code_token_ratio(text) > 0.08:
        return False
    if len(text) > 900:
        return False
    if len(text) > 240 and _punctuation_ratio(text) > 0.18:
        return False
    if len(text) > 240 and _code_token_ratio(text) > 0.18:
        return False
    meta = metadata if isinstance(metadata, dict) else {}
    if meta.get("changed_files"):
        return True
    return True


def _punctuation_ratio(text: str) -> float:
    if not text:
        return 0.0
    punct = sum(1 for ch in text if ch in "{}[]()\\\"=:,")
    return punct / max(1, len(text))


def _code_token_ratio(text: str) -> float:
    tokens = text.split()
    if not tokens:
        return 0.0
    code_like = sum(1 for token in tokens if any(mark in token for mark in ("::", "=>", "()", "=", "{", "}", ";")))
    return code_like / len(tokens)

