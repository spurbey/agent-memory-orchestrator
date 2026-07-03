from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness.anchor_resolution import resolve_anchors
from agent_memory_orchestrator.domain.semantic_harness.identity import normalize_file_path
from agent_memory_orchestrator.domain.semantic_harness.models import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.agent_checkpoint import (
    AgentCheckpointFact,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.agent_checkpoint import (
    AgentCheckpointParseResult,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.agent_checkpoint import (
    AgentCheckpointWorkWindow,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.agent_checkpoint import (
    AgentSemanticCheckpoint,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.agent_checkpoint import (
    checkpoint_fact_to_semantic_fact_proposal,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.agent_checkpoint import (
    parse_agent_semantic_checkpoint,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.attach import (
    SemanticFactAttachResult,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.attach import (
    attach_reviewed_facts_to_store,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.models import (
    SemanticFactProposal,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.parser import (
    parse_semantic_fact_proposals,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.review import (
    SemanticFactReview,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.review import (
    review_semantic_fact_proposals,
)
from agent_memory_orchestrator.domain.semantic_harness.store import HarnessGraphStore


REGION_TO_FILE_FALLBACK_SUFFIXES = frozenset(
    {
        ".adoc",
        ".cfg",
        ".css",
        ".ini",
        ".json",
        ".md",
        ".markdown",
        ".rst",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)


@dataclass(slots=True, frozen=True)
class AgentCheckpointIngestResult:
    checkpoint_id: str
    mode: str
    parse: AgentCheckpointParseResult
    proposals: tuple[SemanticFactProposal, ...]
    review: SemanticFactReview
    artifacts_dir: Path
    attach_result: SemanticFactAttachResult | None = None
    diagnostics: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "mode": self.mode,
            "parse": self.parse.as_dict(),
            "resolved_proposals": [proposal.as_dict() for proposal in self.proposals],
            "review": self.review.as_dict(),
            "attach_result": self.attach_result.as_dict() if self.attach_result else None,
            "diagnostics": list(self.diagnostics),
            "artifacts_dir": str(self.artifacts_dir),
            "summary": {
                "proposals": len(self.proposals),
                "accepted": len(self.review.accepted_facts),
                "review_only": len(self.review.review_only_facts),
                "rejected": len(self.review.rejected_facts),
                "attached": len(self.attach_result.attached_fact_ids) if self.attach_result else 0,
                "graph_mutated": self.attach_result is not None,
            },
        }


def ingest_agent_semantic_checkpoint(
    *,
    checkpoint_file: Path,
    graph: StructuralHarnessGraph,
    out_dir: Path | None = None,
    mode: str = "pending",
    store: HarnessGraphStore | None = None,
) -> AgentCheckpointIngestResult:
    if mode not in {"pending", "attach"}:
        raise ValueError(f"unsupported_checkpoint_ingest_mode:{mode}")
    if mode == "attach" and store is None:
        raise ValueError("attach_mode_requires_graph_store")

    raw = checkpoint_file.read_text(encoding="utf-8")
    parse = parse_agent_semantic_checkpoint(raw)
    checkpoint_id = parse.checkpoint.checkpoint_id if parse.checkpoint else "unknown"
    artifacts_dir = (out_dir or checkpoint_file.parent).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    diagnostics: list[dict[str, str]] = []
    proposals: tuple[SemanticFactProposal, ...] = ()
    if parse.checkpoint is not None:
        proposals, resolution_diagnostics = _resolve_checkpoint_proposals(graph, parse.checkpoint)
        diagnostics.extend(resolution_diagnostics)

    review = review_semantic_fact_proposals(graph=graph, proposals=proposals)
    attach_result = (
        attach_reviewed_facts_to_store(store, facts=review.accepted_facts)
        if mode == "attach" and store is not None
        else None
    )
    result = AgentCheckpointIngestResult(
        checkpoint_id=checkpoint_id,
        mode=mode,
        parse=parse,
        proposals=proposals,
        review=review,
        attach_result=attach_result,
        artifacts_dir=artifacts_dir,
        diagnostics=tuple(diagnostics),
    )
    _write_ingest_artifacts(result, source_file=checkpoint_file)
    return result


def attach_agent_checkpoint_review(
    *,
    review_artifact: Path,
    graph: StructuralHarnessGraph,
    store: HarnessGraphStore,
    out_dir: Path | None = None,
) -> AgentCheckpointIngestResult:
    artifacts_dir = (out_dir or review_artifact.parent).resolve()
    proposals_path = _resolved_proposals_path(review_artifact)
    parsed = parse_semantic_fact_proposals(_read_json(proposals_path))
    review = review_semantic_fact_proposals(graph=graph, proposals=parsed.proposals)
    attach_result = attach_reviewed_facts_to_store(store, facts=review.accepted_facts)
    parse = AgentCheckpointParseResult(checkpoint=None, diagnostics=())
    result = AgentCheckpointIngestResult(
        checkpoint_id=str(_read_json(review_artifact).get("checkpoint_id") or "review-artifact"),
        mode="attach",
        parse=parse,
        proposals=parsed.proposals,
        review=review,
        attach_result=attach_result,
        artifacts_dir=artifacts_dir,
        diagnostics=tuple(parsed.diagnostics),
    )
    _write_ingest_artifacts(result, source_file=None)
    return result


def _resolve_checkpoint_proposals(
    graph: StructuralHarnessGraph,
    checkpoint: AgentSemanticCheckpoint,
) -> tuple[tuple[SemanticFactProposal, ...], tuple[dict[str, str], ...]]:
    proposals: list[SemanticFactProposal] = []
    diagnostics: list[dict[str, str]] = []
    for window in checkpoint.work_windows:
        for fact in window.semantic_facts:
            anchor_ids, anchor_diagnostics = _resolve_fact_anchors(graph, window=window, fact=fact)
            diagnostics.extend(anchor_diagnostics)
            proposals.append(
                checkpoint_fact_to_semantic_fact_proposal(
                    checkpoint=checkpoint,
                    window=window,
                    fact=fact,
                    anchor_node_ids=anchor_ids,
                )
            )
    return tuple(proposals), tuple(diagnostics)


def _resolve_fact_anchors(
    graph: StructuralHarnessGraph,
    *,
    window: AgentCheckpointWorkWindow,
    fact: AgentCheckpointFact,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    resolved: list[str] = []
    diagnostics: list[dict[str, str]] = []
    for anchor_index, anchor in enumerate(fact.anchors):
        file_resolution = resolve_anchors(graph, files=(anchor.path,))
        if not file_resolution.resolved:
            diagnostics.append(_resolution_diag(window, fact, anchor_index, "unresolved_path", anchor.path))
            continue
        if anchor.symbol:
            symbol_resolution = resolve_anchors(graph, symbols=(f"{anchor.path}::{anchor.symbol}",))
            if symbol_resolution.resolved:
                resolved.extend(item.node_id for item in symbol_resolution.resolved)
            else:
                diagnostics.append(_resolution_diag(window, fact, anchor_index, "unresolved_symbol", anchor.symbol))
            continue
        if anchor.line_start or anchor.line_end or anchor.code_region_hint:
            symbol = _symbol_for_line(graph, path=anchor.path, line_start=anchor.line_start, line_end=anchor.line_end)
            if symbol is not None:
                resolved.append(symbol.id)
            elif _can_resolve_region_to_file(anchor.path):
                resolved.extend(item.node_id for item in file_resolution.resolved)
                diagnostics.append(
                    _resolution_diag(window, fact, anchor_index, "region_anchor_resolved_to_file", anchor.path)
                )
            else:
                diagnostics.append(
                    _resolution_diag(window, fact, anchor_index, "ambiguous_line_or_region_anchor", anchor.path)
                )
            continue
        resolved.extend(item.node_id for item in file_resolution.resolved)
    return tuple(dict.fromkeys(resolved)), tuple(diagnostics)


def _symbol_for_line(
    graph: StructuralHarnessGraph,
    *,
    path: str,
    line_start: int,
    line_end: int,
) -> HarnessNode | None:
    normalized_path = normalize_file_path(path).lower()
    start = max(1, int(line_start or line_end or 0))
    end = max(start, int(line_end or line_start or start))
    matches: list[HarnessNode] = []
    for node in graph.nodes_by_kind("Symbol"):
        metadata = node.metadata
        node_path = normalize_file_path(str(metadata.get("path") or "")).lower()
        if node_path != normalized_path:
            continue
        node_start = _int(metadata.get("line_start"))
        node_end = _int(metadata.get("line_end")) or node_start
        if node_start <= start and end <= node_end:
            matches.append(node)
    if not matches:
        return None
    return sorted(matches, key=lambda item: (_span_size(item), str(item.id)))[0]


def _can_resolve_region_to_file(path: str) -> bool:
    normalized = normalize_file_path(path).lower()
    return any(normalized.endswith(suffix) for suffix in REGION_TO_FILE_FALLBACK_SUFFIXES)


def _write_ingest_artifacts(result: AgentCheckpointIngestResult, *, source_file: Path | None) -> None:
    artifacts_dir = result.artifacts_dir
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if source_file is not None:
        (artifacts_dir / "semantic_checkpoint.json").write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")
    _write_json(artifacts_dir / "parse_result.json", result.parse.as_dict())
    _write_json(
        artifacts_dir / "resolved_proposals.json",
        {
            "checkpoint_id": result.checkpoint_id,
            "facts": [proposal.as_dict() for proposal in result.proposals],
            "resolution_diagnostics": list(result.diagnostics),
        },
    )
    review_payload = result.review.as_dict()
    review_payload["checkpoint_id"] = result.checkpoint_id
    _write_json(artifacts_dir / "review_result.json", review_payload)
    _write_json(artifacts_dir / "attach_plan.json", _attach_plan(result))
    (artifacts_dir / "comparison_report.md").write_text(_render_report(result), encoding="utf-8")


def _attach_plan(result: AgentCheckpointIngestResult) -> dict[str, object]:
    return {
        "checkpoint_id": result.checkpoint_id,
        "mode": result.mode,
        "graph_mutated": result.attach_result is not None,
        "accepted_only_fact_ids": [fact.fact_id for fact in result.review.accepted_facts],
        "review_only_fact_ids": [fact.fact_id for fact in result.review.review_only_facts],
        "rejected_fact_ids": [fact.fact_id for fact in result.review.rejected_facts],
        "attach_result": result.attach_result.as_dict() if result.attach_result else None,
    }


def _resolved_proposals_path(review_artifact: Path) -> Path:
    if review_artifact.is_dir():
        return review_artifact / "resolved_proposals.json"
    sibling = review_artifact.parent / "resolved_proposals.json"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"resolved_proposals.json not found next to {review_artifact}")


def _resolution_diag(
    window: AgentCheckpointWorkWindow,
    fact: AgentCheckpointFact,
    anchor_index: int,
    reason: str,
    value: str,
) -> dict[str, str]:
    return {
        "level": "warning",
        "reason": reason,
        "window_id": window.window_id,
        "fact_index": str(fact.index),
        "anchor_index": str(anchor_index),
        "value": value,
    }


def _render_report(result: AgentCheckpointIngestResult) -> str:
    lines = [
        "# Agent Semantic Checkpoint Review",
        "",
        f"- checkpoint: `{result.checkpoint_id}`",
        f"- mode: `{result.mode}`",
        f"- artifacts: `{result.artifacts_dir}`",
        "",
        "## Summary",
        "",
        f"- proposals: {len(result.proposals)}",
        f"- accepted: {len(result.review.accepted_facts)}",
        f"- review_only: {len(result.review.review_only_facts)}",
        f"- rejected: {len(result.review.rejected_facts)}",
        f"- graph_mutated: {result.attach_result is not None}",
        "",
        "## Accepted",
        "",
    ]
    lines.extend(_fact_lines(result.review.accepted_facts))
    lines.extend(["", "## Review Only", ""])
    lines.extend(_fact_lines(result.review.review_only_facts))
    lines.extend(["", "## Rejected", ""])
    lines.extend(_fact_lines(result.review.rejected_facts))
    if result.diagnostics:
        lines.extend(["", "## Resolution Diagnostics", ""])
        for diagnostic in result.diagnostics:
            lines.append(f"- `{diagnostic.get('reason', '')}` {diagnostic.get('value', '')}")
    return "\n".join(lines) + "\n"


def _fact_lines(facts: tuple[Any, ...]) -> list[str]:
    if not facts:
        return ["- none"]
    return [
        f"- `{fact.fact_type}` {fact.text} "
        f"(anchors={len(fact.anchor_node_ids)}, refs={len(fact.source_refs)}, confidence={fact.confidence})"
        for fact in facts
    ]


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _span_size(node: HarnessNode) -> int:
    start = _int(node.metadata.get("line_start"))
    end = _int(node.metadata.get("line_end")) or start
    return max(0, end - start)


__all__ = [
    "AgentCheckpointIngestResult",
    "attach_agent_checkpoint_review",
    "ingest_agent_semantic_checkpoint",
]
