from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from ..code.models import CodeNode
from .models import DecisionThread
from .models import DecisionUnit
from .models import TestRun


@dataclass(slots=True, frozen=True)
class ReasoningEdge:
    source_id: str
    target_id: str
    kind: str
    confidence: float
    evidence_ids: tuple[str, ...]
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "metadata": self.metadata or {},
        }


@dataclass(slots=True, frozen=True)
class ValidationLinkResult:
    decision: DecisionUnit
    edges: tuple[ReasoningEdge, ...]
    diagnostics: tuple[str, ...] = ()


def produced_change_edges(
    *,
    decisions: list[DecisionUnit],
    code_nodes: list[CodeNode],
    thread: DecisionThread,
    max_edges_per_decision: int = 5,
) -> tuple[ReasoningEdge, ...]:
    thread_files = {_norm(path) for path in thread.file_paths}
    edges: list[ReasoningEdge] = []
    for decision in decisions:
        candidates: list[tuple[float, CodeNode, tuple[str, ...]]] = []
        for code_node in code_nodes:
            if thread_files and not any(_same_path(path, code_node.file_path) for path in thread_files):
                continue
            score, reasons = _decision_code_score(decision, code_node, thread)
            if score <= 0.0:
                continue
            candidates.append((score, code_node, reasons))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for score, code_node, reasons in candidates[: max(1, int(max_edges_per_decision))]:
            evidence_ids = _dedupe((*decision.evidence_ids, *code_node.evidence_ids))
            edges.append(
                ReasoningEdge(
                    source_id=decision.id,
                    target_id=code_node.id,
                    kind="PRODUCED_CHANGE_IN",
                    confidence=round(min(decision.confidence, score, 0.9), 6),
                    evidence_ids=evidence_ids,
                    metadata={
                        "thread_id": thread.id,
                        "file_path": code_node.file_path,
                        "link_score": round(score, 6),
                        "link_reasons": list(reasons),
                        "structural_id": code_node.metadata.get("structural_id", ""),
                        "symbol_name": code_node.metadata.get("symbol_name", ""),
                        "ast_status": code_node.ast_status,
                    },
                )
            )
    return tuple(edges)


def code_node_provenance_edges(
    *,
    extraction_run_id: str,
    code_nodes: list[CodeNode],
) -> tuple[ReasoningEdge, ...]:
    edges: list[ReasoningEdge] = []
    for code_node in code_nodes:
        edges.append(
            ReasoningEdge(
                source_id=extraction_run_id,
                target_id=code_node.id,
                kind="CREATED_CODE_NODE",
                confidence=1.0,
                evidence_ids=code_node.evidence_ids,
                metadata={
                    "file_path": code_node.file_path,
                    "commit_id": code_node.commit_id,
                    "hunk_id": code_node.metadata.get("hunk_id", ""),
                    "structural_id": code_node.metadata.get("structural_id", ""),
                    "ast_status": code_node.ast_status,
                },
            )
        )
    return tuple(edges)


def work_change_code_edges(
    *,
    work_changes: list[DecisionUnit],
    code_nodes: list[CodeNode],
) -> tuple[ReasoningEdge, ...]:
    edges: list[ReasoningEdge] = []
    nodes_by_commit: dict[str, list[CodeNode]] = {}
    for node in code_nodes:
        nodes_by_commit.setdefault(node.commit_id, []).append(node)

    for work_change in work_changes:
        commit_id = str(work_change.metadata.get("commit_id") or "")
        files = {str(path) for path in work_change.metadata.get("git_changed_files", ())}
        for code_node in nodes_by_commit.get(commit_id, ()):
            if files and code_node.file_path not in files:
                continue
            evidence_ids = _dedupe((*work_change.evidence_ids, *code_node.evidence_ids))
            edges.append(
                ReasoningEdge(
                    source_id=work_change.id,
                    target_id=code_node.id,
                    kind="PRODUCED_CHANGE_IN",
                    confidence=1.0,
                    evidence_ids=evidence_ids,
                    metadata={
                        "commit_id": commit_id,
                        "file_path": code_node.file_path,
                        "hunk_id": code_node.metadata.get("hunk_id", ""),
                        "structural_id": code_node.metadata.get("structural_id", ""),
                        "ast_type": code_node.ast_type,
                        "ast_status": code_node.ast_status,
                    },
                )
            )
    return tuple(edges)


def work_change_commit_edges(*, work_changes: list[DecisionUnit]) -> tuple[ReasoningEdge, ...]:
    edges: list[ReasoningEdge] = []
    for work_change in work_changes:
        commit_id = str(work_change.metadata.get("commit_id") or "")
        if not commit_id:
            continue
        edges.append(
            ReasoningEdge(
                source_id=work_change.id,
                target_id=f"commit:{commit_id}",
                kind="COMMITTED_AS",
                confidence=1.0,
                evidence_ids=work_change.evidence_ids,
                metadata={
                    "commit_id": commit_id,
                    "full_sha": str(work_change.metadata.get("full_sha") or ""),
                    "commit_message": str(work_change.metadata.get("commit_message") or ""),
                },
            )
        )
    return tuple(edges)


def code_node_commit_edges(*, code_nodes: list[CodeNode]) -> tuple[ReasoningEdge, ...]:
    edges: list[ReasoningEdge] = []
    for code_node in code_nodes:
        edges.append(
            ReasoningEdge(
                source_id=code_node.id,
                target_id=f"commit:{code_node.commit_id}",
                kind="LINKED_TO_COMMIT",
                confidence=1.0,
                evidence_ids=code_node.evidence_ids,
                metadata={
                    "commit_id": code_node.commit_id,
                    "file_path": code_node.file_path,
                    "hunk_id": code_node.metadata.get("hunk_id", ""),
                    "structural_id": code_node.metadata.get("structural_id", ""),
                },
            )
        )
    return tuple(edges)


def validation_edges_for_test(
    *,
    decision: DecisionUnit,
    test_run: TestRun,
    event_order: dict[str, int],
    write_event_ids: tuple[str, ...],
) -> ValidationLinkResult:
    diagnostics: list[str] = []
    test_event_id = str(test_run.metadata.get("event_id") or "")
    test_index = event_order.get(test_event_id)
    write_indexes = [event_order[event_id] for event_id in write_event_ids if event_id in event_order]
    if test_index is None:
        return ValidationLinkResult(decision=decision, edges=(), diagnostics=("missing_test_event_order",))
    if not write_indexes:
        return ValidationLinkResult(decision=decision, edges=(), diagnostics=("missing_write_event_order",))
    if test_index <= max(write_indexes):
        return ValidationLinkResult(decision=decision, edges=(), diagnostics=("test_before_write",))

    result = test_run.result.lower().strip()
    evidence_ids = _dedupe((*decision.evidence_ids, *test_run.evidence_ids))
    if result == "pass":
        edge = ReasoningEdge(
            source_id=decision.id,
            target_id=test_run.id,
            kind="VALIDATED_BY",
            confidence=0.9,
            evidence_ids=evidence_ids,
            metadata={"test_event_id": test_event_id, "write_event_ids": list(write_event_ids)},
        )
        bumped = replace(decision, confidence=max(decision.confidence, 0.9))
        return ValidationLinkResult(decision=bumped, edges=(edge,))
    if result == "fail":
        edge = ReasoningEdge(
            source_id=decision.id,
            target_id=test_run.id,
            kind="FAILED_VALIDATION",
            confidence=0.9,
            evidence_ids=evidence_ids,
            metadata={"test_event_id": test_event_id, "write_event_ids": list(write_event_ids)},
        )
        return ValidationLinkResult(decision=decision, edges=(edge,))
    diagnostics.append(f"unknown_test_result:{test_run.result}")
    return ValidationLinkResult(decision=decision, edges=(), diagnostics=tuple(diagnostics))


def _norm(value: str) -> str:
    return value.replace("\\", "/").strip().strip('"').lower()


def _same_path(left: str, right: str) -> bool:
    left_norm = _norm(left)
    right_norm = _norm(right)
    return left_norm == right_norm or left_norm.endswith(f"/{right_norm}") or right_norm.endswith(f"/{left_norm}")


def _decision_code_score(
    decision: DecisionUnit,
    code_node: CodeNode,
    thread: DecisionThread,
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = ["same_thread_file"]
    score = 0.20
    decision_evidence = set(decision.evidence_ids).union(thread.evidence_ids)
    code_evidence = set(code_node.evidence_ids)
    if decision_evidence.intersection(code_evidence):
        score += 0.45
        reasons.append("shared_evidence")

    query_terms = _terms(" ".join([decision.summary, thread.topic, " ".join(thread.file_paths)]))
    code_terms = _terms(
        " ".join(
            [
                code_node.file_path,
                code_node.ast_type,
                code_node.content,
                str(code_node.metadata.get("symbol_name") or ""),
                str(code_node.metadata.get("symbol_kind") or ""),
                str(code_node.metadata.get("structural_id") or ""),
            ]
        )
    )
    if query_terms and code_terms:
        overlap = query_terms.intersection(code_terms)
        lexical = len(overlap) / max(1, min(len(query_terms), len(code_terms)))
        if lexical:
            score += min(0.25, lexical * 0.25)
            reasons.append(f"lexical_overlap:{','.join(sorted(overlap)[:5])}")
    symbol = str(code_node.metadata.get("symbol_name") or "").lower()
    if symbol and any(term in symbol for term in query_terms):
        score += 0.10
        reasons.append("symbol_match")
    if code_node.ast_status == "parsed":
        score += 0.10
        reasons.append("parsed_ast")
    if len(code_node.content.splitlines()) <= 3:
        score += 0.05
        reasons.append("focused_code_region")

    # File-only matches are allowed as a low-confidence fallback only when the
    # region is parsed/focused. This prevents one broad thread from linking to
    # every hunk in a file without any semantic or provenance signal.
    if reasons == ["same_thread_file"] and len(code_node.content.splitlines()) > 3:
        return 0.0, ()
    return score, tuple(reasons)


def _terms(text: str) -> set[str]:
    stopwords = {
        "about",
        "after",
        "again",
        "also",
        "and",
        "are",
        "back",
        "because",
        "been",
        "before",
        "being",
        "but",
        "can",
        "code",
        "did",
        "does",
        "file",
        "for",
        "from",
        "has",
        "have",
        "how",
        "into",
        "its",
        "now",
        "only",
        "should",
        "that",
        "the",
        "then",
        "this",
        "use",
        "used",
        "using",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "why",
        "will",
        "with",
        "your",
    }
    out: set[str] = set()
    for raw in re.split(r"[^A-Za-z0-9_./-]+", text.lower()):
        for token in re.split(r"[/_.-]+", raw):
            if len(token) <= 2 or token in stopwords:
                continue
            if re.fullmatch(r"[0-9a-f]{12,40}", token):
                continue
            out.add(token)
    return out


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return tuple(out)
