from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....domain.semantic_harness import HarnessQueryRequest
from ....domain.semantic_harness import HarnessQueryResponse
from ....domain.semantic_harness import StructuralHarnessGraph
from .repository import RepoBootstrapOptions
from .structural import StructuralHarnessService


@dataclass(slots=True, frozen=True)
class StructuralEvalCase:
    case_id: str
    request: HarnessQueryRequest
    expected_status: str
    required_card_types: tuple[str, ...] = ()
    required_title_terms: tuple[str, ...] = ()
    forbidden_card_types: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class StructuralEvalCaseResult:
    case_id: str
    passed: bool
    expected_status: str
    actual_status: str
    card_types: tuple[str, ...]
    card_titles: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    response: HarnessQueryResponse

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "expected_status": self.expected_status,
            "actual_status": self.actual_status,
            "card_types": list(self.card_types),
            "card_titles": list(self.card_titles),
            "failure_reasons": list(self.failure_reasons),
            "response": self.response.as_dict(),
        }


@dataclass(slots=True, frozen=True)
class StructuralEvalReport:
    repo_root: str
    repo_id: str
    file_count: int
    skipped_count: int
    node_counts: dict[str, int]
    edge_counts: dict[str, int]
    cases: tuple[StructuralEvalCaseResult, ...]
    passed: bool
    contract_judgment: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "repo_id": self.repo_id,
            "file_count": self.file_count,
            "skipped_count": self.skipped_count,
            "node_counts": self.node_counts,
            "edge_counts": self.edge_counts,
            "cases": [case.as_dict() for case in self.cases],
            "passed": self.passed,
            "contract_judgment": self.contract_judgment,
        }


class StructuralHarnessEvalService:
    def __init__(self, harness: StructuralHarnessService | None = None) -> None:
        self.harness = harness or StructuralHarnessService()

    def evaluate_repo(
        self,
        repo_root: str | Path,
        *,
        repo_id: str,
        cases: tuple[StructuralEvalCase, ...],
        options: RepoBootstrapOptions | None = None,
    ) -> StructuralEvalReport:
        bootstrap = self.harness.bootstrap_repo(repo_root, repo_id=repo_id, options=options)
        node_counts = dict(sorted(Counter(node.kind for node in bootstrap.graph.nodes).items()))
        edge_counts = dict(sorted(Counter(edge.kind for edge in bootstrap.graph.edges).items()))
        results = tuple(_run_case(self.harness, bootstrap.graph, case) for case in cases)
        structural_count = sum(
            node_counts.get(kind, 0)
            for kind in ("File", "Symbol", "CodeRegion")
        )
        version_count = sum(
            node_counts.get(kind, 0)
            for kind in ("FileVersion", "SymbolVersion", "CodeRegionVersion")
        )
        raw_ast_flood = "CodeNode" in node_counts or "CodeHunk" in node_counts
        return StructuralEvalReport(
            repo_root=str(bootstrap.repo_root),
            repo_id=bootstrap.graph.repo_id,
            file_count=bootstrap.file_count,
            skipped_count=bootstrap.skipped_count,
            node_counts=node_counts,
            edge_counts=edge_counts,
            cases=results,
            passed=all(result.passed for result in results),
            contract_judgment={
                "phase": "phase1_structural",
                "raw_ast_flood": raw_ast_flood,
                "structural_versions_present": structural_count == version_count,
                "structural_node_count": structural_count,
                "version_node_count": version_count,
                "qwen_used": False,
                "vector_used": False,
                "history_imported": False,
                "expected_status_without_history": "partial_structural",
            },
        )


def _run_case(
    harness: StructuralHarnessService,
    graph: StructuralHarnessGraph,
    case: StructuralEvalCase,
) -> StructuralEvalCaseResult:
    response = harness.query(graph, case.request)
    failures: list[str] = []
    if response.status != case.expected_status:
        failures.append(f"status:{response.status}!={case.expected_status}")
    card_types = tuple(card.type for card in response.cards)
    card_titles = tuple(card.title for card in response.cards)
    for required_type in case.required_card_types:
        if required_type not in card_types:
            failures.append(f"missing_card_type:{required_type}")
    for forbidden_type in case.forbidden_card_types:
        if forbidden_type in card_types:
            failures.append(f"forbidden_card_type:{forbidden_type}")
    titles = " | ".join(card.title.lower() for card in response.cards)
    for term in case.required_title_terms:
        if term.lower() not in titles:
            failures.append(f"missing_title_term:{term}")
    return StructuralEvalCaseResult(
        case_id=case.case_id,
        passed=not failures,
        expected_status=case.expected_status,
        actual_status=response.status,
        card_types=card_types,
        card_titles=card_titles,
        failure_reasons=tuple(failures),
        response=response,
    )


__all__ = [
    "StructuralEvalCase",
    "StructuralEvalCaseResult",
    "StructuralEvalReport",
    "StructuralHarnessEvalService",
]
