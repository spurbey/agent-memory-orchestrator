from __future__ import annotations

import re
from dataclasses import dataclass

from agent_memory_orchestrator.domain.semantic_harness import HarnessCard
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import harness_card_id


MIN_FOCUS_LEXICAL_SCORE = 0.05
MIN_FOCUS_QUERY_TERMS = 2


@dataclass(slots=True, frozen=True)
class SearchFocusCandidate:
    node_id: str
    path: str
    label: str
    score: float
    lexical_score: float
    role_score: float


def build_broad_search_focus_card(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    seen_card_ids: set[str],
) -> HarnessCard | None:
    """Return one compact focus card for broad search results.

    The card is intentionally not a graph-truth claim. It ranks already-visible,
    graph-grounded search hits so the agent has a better first inspection order.
    """

    candidates = rank_search_focus_candidates(graph=graph, request=request)
    if len(candidates) < 2:
        return None
    visible = candidates[:3]
    if max(candidate.lexical_score for candidate in visible) < MIN_FOCUS_LEXICAL_SCORE:
        return None
    paths = tuple(candidate.path for candidate in visible)
    card_id = harness_card_id(graph.repo_id, request.session_id, request.intent, ("search_focus", *paths))
    if card_id in seen_card_ids:
        return None
    target = visible[0].path
    suffix = "" if len(visible) == 1 else f" + {len(visible) - 1} more"
    evidence = tuple(
        {
            "node_id": candidate.node_id,
            "kind": "File",
            "path": candidate.path,
            "rank": str(index),
            "score": f"{candidate.score:.4f}",
            "lexical_score": f"{candidate.lexical_score:.4f}",
            "role_score": f"{candidate.role_score:.2f}",
        }
        for index, candidate in enumerate(visible, start=1)
    )
    confidence = round(min(0.82, 0.75 + max(candidate.lexical_score for candidate in visible) * 0.25), 2)
    return HarnessCard(
        card_id=card_id,
        type="next_file",
        title=f"Focus broad search on {target}{suffix}",
        why="Broad search matched many files; these graph-grounded hits have the strongest path-role and query-token focus.",
        evidence=evidence,
        risk="Search-focus signal only; inspect before editing and do not treat it as proof of causality.",
        confidence=confidence,
        next_action=f"Open {target} first, then compare with the other focused search hits if needed.",
    )


def rank_search_focus_candidates(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
) -> tuple[SearchFocusCandidate, ...]:
    file_by_path = _file_nodes_by_path(graph)
    query_terms = _terms(str(request.recent_tool_result.get("command") or ""))
    if len(query_terms) < MIN_FOCUS_QUERY_TERMS:
        return ()
    candidates: list[SearchFocusCandidate] = []
    for path in dict.fromkeys(_normalize_path(path) for path in request.files if path):
        node = file_by_path.get(path)
        if node is None:
            continue
        lexical_score = _lexical_score(path=path, label=node.label, summary=node.summary, query_terms=query_terms)
        role_score = _path_role_score(path, str(request.recent_tool_result.get("command") or ""))
        score = round((0.50 * lexical_score) + (0.50 * role_score), 4)
        candidates.append(
            SearchFocusCandidate(
                node_id=node.id,
                path=path,
                label=node.label,
                score=score,
                lexical_score=lexical_score,
                role_score=role_score,
            )
        )
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.path))
    return tuple(candidates)


def _file_nodes_by_path(graph: StructuralHarnessGraph) -> dict[str, HarnessNode]:
    out: dict[str, HarnessNode] = {}
    for node in graph.nodes_by_kind("File"):
        path = _normalize_path(str(node.metadata.get("path") or node.label))
        if path:
            out[path] = node
    return out


def _lexical_score(*, path: str, label: str, summary: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    candidate_terms = _terms(" ".join((path, label, summary)))
    return round(len(query_terms & candidate_terms) / max(1, len(query_terms)), 4)


def _path_role_score(path: str, command: str) -> float:
    lowered = command.lower()
    if path.startswith("tests/"):
        return 0.90 if _mentions_any(lowered, ("test", "pytest", "assert", "validation")) else 0.32
    if path.startswith("src/"):
        return 1.0
    if path.startswith("docs/"):
        return 0.82 if _mentions_any(lowered, ("doc", "readme", "contract", "plan")) else 0.45
    if path.startswith(("npm/", "scripts/")):
        return 0.70
    return 0.50


def _mentions_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _terms(text: str) -> set[str]:
    normalized = _split_identifier_text(text)
    return {
        token
        for token in re.findall(r"[A-Za-z0-9]+", normalized.lower())
        if len(token) >= 3 and token not in _STOP_TERMS and token not in _LOW_SIGNAL_TERMS
    }


def _split_identifier_text(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.sub(r"[_/\\.:|()\"'`\[\]{}<>-]+", " ", text)


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("'\"`.,:;)]}").lower()


_STOP_TERMS = frozenset(
    {
        "agent",
        "and",
        "are",
        "but",
        "class",
        "def",
        "docs",
        "for",
        "from",
        "harness",
        "how",
        "import",
        "memory",
        "not",
        "orchestrator",
        "pytest",
        "python",
        "return",
        "self",
        "semantic",
        "src",
        "test",
        "tests",
        "that",
        "the",
        "this",
        "what",
        "with",
    }
)

_LOW_SIGNAL_TERMS = frozenset(
    {
        "add",
        "build",
        "change",
        "changed",
        "check",
        "fix",
        "get",
        "make",
        "patch",
        "set",
        "update",
        "use",
    }
)


__all__ = ["SearchFocusCandidate", "build_broad_search_focus_card", "rank_search_focus_candidates"]
