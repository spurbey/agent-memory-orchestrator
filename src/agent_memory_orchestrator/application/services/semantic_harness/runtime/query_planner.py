from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import EdgeExpansion
from agent_memory_orchestrator.domain.semantic_harness import GraphSeed
from agent_memory_orchestrator.domain.semantic_harness import GraphSlicePlan
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness.identity import normalize_file_path
from agent_memory_orchestrator.domain.semantic_harness.query_modes.question_classifier import classify_context_questions
from agent_memory_orchestrator.domain.semantic_harness.query_modes.rank_tool_hits import parse_rank_tool_lines

_CONTEXT_NODE_LIMIT = 128
_CONTEXT_EDGE_LIMIT = 256
_RANK_PATH_LIMIT = 250
_RANK_NODE_LIMIT = 4_000
_RANK_EDGE_LIMIT = 6_000


def plan_query_evidence(repo_id: str, request: HarnessQueryRequest, *, mode: str) -> GraphSlicePlan | None:
    if mode == "context_for_anchor":
        return _context_plan(repo_id, request)
    if mode == "rank_tool_hits":
        return _rank_plan(repo_id, request)
    return None


def _context_plan(repo_id: str, request: HarnessQueryRequest) -> GraphSlicePlan:
    files = tuple(path for value in request.files if (path := _clean_path(value)))
    default_path_hint = files[0] if len(files) == 1 else ""
    seeds = [GraphSeed(kind="file", value=path) for path in files]
    seeds.extend(_symbol_seed(symbol, default_path_hint=default_path_hint) for symbol in request.symbols if symbol.strip())

    question_types = {
        question_type
        for classification in classify_context_questions(request.questions)
        for question_type in classification.types
    }
    expansions: list[EdgeExpansion] = []
    if question_types & {"validation", "risk"}:
        expansions.append(EdgeExpansion(kind="VALIDATED_BY", direction="outgoing", max_neighbors=32))
    if "risk" in question_types:
        expansions.append(EdgeExpansion(kind="CO_CHANGED_WITH", direction="outgoing", max_neighbors=32))
    if "usage" in question_types:
        for kind in ("CALLS", "IMPORTS"):
            expansions.append(EdgeExpansion(kind=kind, direction="outgoing", max_neighbors=64))
            expansions.append(EdgeExpansion(kind=kind, direction="incoming", max_neighbors=64))
    return GraphSlicePlan(
        repo_id=repo_id,
        purpose="context_for_anchor",
        seeds=tuple(dict.fromkeys(seeds)),
        expansions=tuple(dict.fromkeys(expansions)),
        max_nodes=_CONTEXT_NODE_LIMIT,
        max_edges=_CONTEXT_EDGE_LIMIT,
    )


def _rank_plan(repo_id: str, request: HarnessQueryRequest) -> GraphSlicePlan:
    paths = tuple(
        dict.fromkeys(
            line.file_path
            for line in parse_rank_tool_lines(request.recent_tool_result)
            if line.file_path
        )
    )[:_RANK_PATH_LIMIT]
    return GraphSlicePlan(
        repo_id=repo_id,
        purpose="rank_tool_hits",
        seeds=tuple(GraphSeed(kind="file", value=path) for path in paths),
        expansions=(
            EdgeExpansion(kind="DEFINES", direction="outgoing", max_neighbors=128),
            EdgeExpansion(kind="CONTAINS", direction="outgoing", max_neighbors=128),
        ),
        max_nodes=_RANK_NODE_LIMIT,
        max_edges=_RANK_EDGE_LIMIT,
    )


def _symbol_seed(value: str, *, default_path_hint: str) -> GraphSeed:
    safe = str(value or "").strip()
    if "::" not in safe:
        return GraphSeed(kind="symbol", value=safe, path_hint=default_path_hint)
    path, symbol = safe.rsplit("::", 1)
    return GraphSeed(kind="symbol", value=symbol.strip(), path_hint=_clean_path(path))


def _clean_path(value: str) -> str:
    return normalize_file_path(str(value or "").strip())


__all__ = ["plan_query_evidence"]
