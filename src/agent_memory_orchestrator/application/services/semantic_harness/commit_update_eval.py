from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commit_update import CommitUpdateService
from .repository import RepoBootstrapOptions


@dataclass(slots=True, frozen=True)
class CommitUpdateEvalCase:
    case_id: str
    commit_sha: str
    expected_min_hunks: int = 1
    required_node_kinds: tuple[str, ...] = ()
    required_edge_kinds: tuple[str, ...] = ()
    required_mapping_statuses: tuple[str, ...] = ()
    session_id: str = ""


@dataclass(slots=True, frozen=True)
class CommitUpdateEvalCaseResult:
    case_id: str
    passed: bool
    commit_sha: str
    commit_message: str
    diff_hunk_count: int
    node_counts: dict[str, int]
    edge_counts: dict[str, int]
    mapping_status_counts: dict[str, int]
    failure_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "commit_sha": self.commit_sha,
            "commit_message": self.commit_message,
            "diff_hunk_count": self.diff_hunk_count,
            "node_counts": self.node_counts,
            "edge_counts": self.edge_counts,
            "mapping_status_counts": self.mapping_status_counts,
            "failure_reasons": list(self.failure_reasons),
        }


@dataclass(slots=True, frozen=True)
class CommitUpdateEvalReport:
    repo_root: str
    repo_id: str
    cases: tuple[CommitUpdateEvalCaseResult, ...]
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


class CommitUpdateEvalService:
    def __init__(self, update_service: CommitUpdateService | None = None) -> None:
        self.update_service = update_service or CommitUpdateService()

    def evaluate_repo(
        self,
        repo_root: str | Path,
        *,
        repo_id: str,
        cases: tuple[CommitUpdateEvalCase, ...],
        options: RepoBootstrapOptions | None = None,
    ) -> CommitUpdateEvalReport:
        results = tuple(
            self._run_case(repo_root, repo_id=repo_id, case=case, options=options)
            for case in cases
        )
        return CommitUpdateEvalReport(
            repo_root=str(Path(repo_root).resolve()),
            repo_id=repo_id,
            cases=results,
            passed=all(result.passed for result in results),
            contract_judgment={
                "phase": "commit_update_deterministic",
                "qwen_used": False,
                "storage_mutated": False,
                "semantic_review_expected": {"accepted": 0, "review_only": 0, "rejected": 0, "quarantined": 0},
            },
        )

    def _run_case(
        self,
        repo_root: str | Path,
        *,
        repo_id: str,
        case: CommitUpdateEvalCase,
        options: RepoBootstrapOptions | None,
    ) -> CommitUpdateEvalCaseResult:
        result = self.update_service.build_delta_for_commit(
            repo_root,
            case.commit_sha,
            repo_id=repo_id,
            session_id=case.session_id or case.case_id,
            options=options,
        )
        node_counts = dict(sorted(Counter(node.kind for node in result.delta.created_nodes).items()))
        edge_counts = dict(sorted(Counter(edge.kind for edge in result.delta.created_edges).items()))
        mapping_status_counts = dict(sorted(Counter(mapping.status for mapping in result.delta.hunk_mappings).items()))
        failures = _case_failures(
            case=case,
            diff_hunk_count=result.diff_hunk_count,
            node_counts=node_counts,
            edge_counts=edge_counts,
            mapping_status_counts=mapping_status_counts,
        )
        return CommitUpdateEvalCaseResult(
            case_id=case.case_id,
            passed=not failures,
            commit_sha=result.commit_sha,
            commit_message=result.commit_message,
            diff_hunk_count=result.diff_hunk_count,
            node_counts=node_counts,
            edge_counts=edge_counts,
            mapping_status_counts=mapping_status_counts,
            failure_reasons=tuple(failures),
        )


def _case_failures(
    *,
    case: CommitUpdateEvalCase,
    diff_hunk_count: int,
    node_counts: dict[str, int],
    edge_counts: dict[str, int],
    mapping_status_counts: dict[str, int],
) -> list[str]:
    failures: list[str] = []
    if diff_hunk_count < case.expected_min_hunks:
        failures.append(f"diff_hunk_count:{diff_hunk_count}<expected_min:{case.expected_min_hunks}")
    for kind in case.required_node_kinds:
        if node_counts.get(kind, 0) <= 0:
            failures.append(f"missing_node_kind:{kind}")
    for kind in case.required_edge_kinds:
        if edge_counts.get(kind, 0) <= 0:
            failures.append(f"missing_edge_kind:{kind}")
    for status in case.required_mapping_statuses:
        if mapping_status_counts.get(status, 0) <= 0:
            failures.append(f"missing_mapping_status:{status}")
    return failures


__all__ = [
    "CommitUpdateEvalCase",
    "CommitUpdateEvalCaseResult",
    "CommitUpdateEvalReport",
    "CommitUpdateEvalService",
]
