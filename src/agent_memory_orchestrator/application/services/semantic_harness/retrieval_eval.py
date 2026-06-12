from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....domain.semantic_harness import HarnessQueryRequest
from ....domain.semantic_harness import HarnessQueryResponse
from ....domain.semantic_harness import StructuralHarnessGraph
from .repository import RepoBootstrapOptions
from .structural import StructuralHarnessService


@dataclass(slots=True, frozen=True)
class RetrievalEvalCase:
    case_id: str
    request: HarnessQueryRequest
    expected_status: str
    expected_lexical_used: bool | None = None
    expected_vector_used: bool | None = None
    expected_first_card_type: str = ""
    expected_first_card_title: str = ""
    required_card_types: tuple[str, ...] = ()
    required_title_terms: tuple[str, ...] = ()
    required_warning_terms: tuple[str, ...] = ()
    forbidden_warning_terms: tuple[str, ...] = ()
    required_next_action_targets: tuple[str, ...] = ()
    min_card_count: int = 0


@dataclass(slots=True, frozen=True)
class RetrievalEvalCaseResult:
    case_id: str
    passed: bool
    expected_status: str
    actual_status: str
    lexical_used: bool
    vector_used: bool
    graph_grounded_card_count: int
    card_types: tuple[str, ...]
    card_titles: tuple[str, ...]
    next_action_targets: tuple[str, ...]
    warnings: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    response: HarnessQueryResponse

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "expected_status": self.expected_status,
            "actual_status": self.actual_status,
            "lexical_used": self.lexical_used,
            "vector_used": self.vector_used,
            "graph_grounded_card_count": self.graph_grounded_card_count,
            "card_types": list(self.card_types),
            "card_titles": list(self.card_titles),
            "next_action_targets": list(self.next_action_targets),
            "warnings": list(self.warnings),
            "failure_reasons": list(self.failure_reasons),
            "response": self.response.as_dict(),
        }


@dataclass(slots=True, frozen=True)
class RetrievalEvalReport:
    repo_root: str
    repo_id: str
    cases: tuple[RetrievalEvalCaseResult, ...]
    passed: bool
    contract_judgment: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "repo_id": self.repo_id,
            "cases": [case.as_dict() for case in self.cases],
            "passed": self.passed,
            "contract_judgment": self.contract_judgment,
        }


class RetrievalHarnessEvalService:
    def __init__(self, harness: StructuralHarnessService | None = None) -> None:
        self.harness = harness or StructuralHarnessService()

    def evaluate_repo(
        self,
        repo_root: str | Path,
        *,
        repo_id: str,
        cases: tuple[RetrievalEvalCase, ...],
        options: RepoBootstrapOptions | None = None,
    ) -> RetrievalEvalReport:
        bootstrap = self.harness.bootstrap_repo(repo_root, repo_id=repo_id, options=options)
        results = tuple(_run_case(self.harness, bootstrap.graph, case) for case in cases)
        vector_used = any(result.vector_used for result in results)
        return RetrievalEvalReport(
            repo_root=str(bootstrap.repo_root),
            repo_id=bootstrap.graph.repo_id,
            cases=results,
            passed=all(result.passed for result in results),
            contract_judgment={
                "phase": "retrieval_mvp",
                "exact_anchor_first": True,
                "lexical_candidate_discovery": True,
                "vector_candidate_discovery": True,
                "vector_backend": "hash_token_char_cosine_v1",
                "vector_used": vector_used,
                "llm_used": False,
                "candidates_must_ground_to_graph": True,
            },
        )


def _run_case(
    harness: StructuralHarnessService,
    graph: StructuralHarnessGraph,
    case: RetrievalEvalCase,
) -> RetrievalEvalCaseResult:
    response = harness.query(graph, case.request)
    card_types = tuple(card.type for card in response.cards)
    card_titles = tuple(card.title for card in response.cards)
    next_action_targets = tuple(action.target for action in response.next_actions)
    lexical_used = "candidate_discovery:lexical_projection" in response.warnings
    vector_used = "candidate_discovery:vector_projection" in response.warnings
    graph_grounded_card_count = _graph_grounded_card_count(graph, response)
    failures = _case_failures(
        case=case,
        response=response,
        card_types=card_types,
        card_titles=card_titles,
        next_action_targets=next_action_targets,
        lexical_used=lexical_used,
        vector_used=vector_used,
        graph_grounded_card_count=graph_grounded_card_count,
    )
    return RetrievalEvalCaseResult(
        case_id=case.case_id,
        passed=not failures,
        expected_status=case.expected_status,
        actual_status=response.status,
        lexical_used=lexical_used,
        vector_used=vector_used,
        graph_grounded_card_count=graph_grounded_card_count,
        card_types=card_types,
        card_titles=card_titles,
        next_action_targets=next_action_targets,
        warnings=response.warnings,
        failure_reasons=tuple(failures),
        response=response,
    )


def _case_failures(
    *,
    case: RetrievalEvalCase,
    response: HarnessQueryResponse,
    card_types: tuple[str, ...],
    card_titles: tuple[str, ...],
    next_action_targets: tuple[str, ...],
    lexical_used: bool,
    vector_used: bool,
    graph_grounded_card_count: int,
) -> list[str]:
    failures: list[str] = []
    if response.status != case.expected_status:
        failures.append(f"status:{response.status}!={case.expected_status}")
    if case.expected_lexical_used is not None and lexical_used is not case.expected_lexical_used:
        failures.append(f"lexical_used:{lexical_used}!={case.expected_lexical_used}")
    if case.expected_vector_used is not None and vector_used is not case.expected_vector_used:
        failures.append(f"vector_used:{vector_used}!={case.expected_vector_used}")
    if len(response.cards) < case.min_card_count:
        failures.append(f"card_count:{len(response.cards)}<min:{case.min_card_count}")
    if case.expected_first_card_type and (not card_types or card_types[0] != case.expected_first_card_type):
        actual = card_types[0] if card_types else ""
        failures.append(f"first_card_type:{actual}!={case.expected_first_card_type}")
    if case.expected_first_card_title and (not card_titles or card_titles[0] != case.expected_first_card_title):
        actual = card_titles[0] if card_titles else ""
        failures.append(f"first_card_title:{actual}!={case.expected_first_card_title}")
    for required_type in case.required_card_types:
        if required_type not in card_types:
            failures.append(f"missing_card_type:{required_type}")
    titles = " | ".join(card_titles).lower()
    for term in case.required_title_terms:
        if term.lower() not in titles:
            failures.append(f"missing_title_term:{term}")
    warnings = " | ".join(response.warnings).lower()
    for term in case.required_warning_terms:
        if term.lower() not in warnings:
            failures.append(f"missing_warning:{term}")
    for term in case.forbidden_warning_terms:
        if term.lower() in warnings:
            failures.append(f"forbidden_warning:{term}")
    for target in case.required_next_action_targets:
        if target not in next_action_targets:
            failures.append(f"missing_next_action_target:{target}")
    if graph_grounded_card_count != len(response.cards):
        failures.append(f"ungrounded_cards:{len(response.cards) - graph_grounded_card_count}")
    return failures


def _graph_grounded_card_count(graph: StructuralHarnessGraph, response: HarnessQueryResponse) -> int:
    node_ids = {node.id for node in graph.nodes}
    count = 0
    for card in response.cards:
        if any(evidence.get("node_id") in node_ids for evidence in card.evidence):
            count += 1
    return count


__all__ = [
    "RetrievalEvalCase",
    "RetrievalEvalCaseResult",
    "RetrievalEvalReport",
    "RetrievalHarnessEvalService",
]
