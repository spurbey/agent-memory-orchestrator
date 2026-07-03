from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness.identity import file_id
from agent_memory_orchestrator.domain.semantic_harness.models import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness.models import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.models import SOURCE_AGENT_SESSION
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.models import SPAN_VALIDATED_COMMITTED
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.models import SemanticFactSourceRef
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.packets import SemanticEvidencePacket
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.packets import build_semantic_evidence_packet
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.parser import SemanticFactProposalParse
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.parser import parse_semantic_fact_proposals
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.provider_contract import (
    build_repo_semantic_fact_prompt,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.provider_contract import (
    repo_semantic_fact_contract_hash,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.provider_contract import (
    repo_semantic_fact_output_schema,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.review import SemanticFactReview
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts.review import review_semantic_fact_proposals

from .provider import ExternalProviderUnavailable
from .provider import OpenAICompatibleJsonProvider


@dataclass(slots=True, frozen=True)
class RepoSemanticProducerEvalReport:
    selected_job_id: str
    packet_id: str
    commit_sha: str
    provider_model: str
    provider_model_env: str
    old_accepted_nodes: tuple[dict[str, Any], ...]
    parse: SemanticFactProposalParse
    review: SemanticFactReview
    artifacts_dir: Path
    provider_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_job_id": self.selected_job_id,
            "packet_id": self.packet_id,
            "commit_sha": self.commit_sha,
            "provider_model": self.provider_model,
            "provider_model_env": self.provider_model_env,
            "old_accepted_nodes": list(self.old_accepted_nodes),
            "new_proposed_facts": [proposal.as_dict() for proposal in self.parse.proposals],
            "parse_diagnostics": list(self.parse.diagnostics),
            "review": self.review.as_dict(),
            "provider_error": self.provider_error,
            "artifacts_dir": str(self.artifacts_dir),
            "summary": {
                "old_accepted_nodes": len(self.old_accepted_nodes),
                "new_proposed_facts": len(self.parse.proposals),
                "accepted": len(self.review.accepted_facts),
                "review_only": len(self.review.review_only_facts),
                "rejected": len(self.review.rejected_facts),
                "anchor_coverage": _coverage(
                    proposal.anchor_node_ids for proposal in self.parse.proposals
                ),
                "source_ref_coverage": _coverage(
                    tuple(ref.ref_id for ref in proposal.source_refs) for proposal in self.parse.proposals
                ),
                "better_suited_for_context_for_anchor": _better_suited_for_context_for_anchor(self.review),
            },
        }


def run_repo_semantic_producer_eval(
    *,
    job_root: Path,
    out_dir: Path,
    provider: OpenAICompatibleJsonProvider,
    max_tokens: int = 1200,
) -> RepoSemanticProducerEvalReport:
    fixture = _load_fixture(job_root)
    graph, packet, old_nodes = _build_packet_and_graph(fixture)
    prompt = build_repo_semantic_fact_prompt(packet.as_dict())
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_json(out_dir / "repo_semantic_packet.json", packet.as_dict())
    _write_json(
        out_dir / "provider_request_redacted.json",
        {
            "provider": provider.config.as_diagnostic_dict(),
            "contract_hash": repo_semantic_fact_contract_hash(),
            "prompt_sha256": _sha256(prompt),
            "prompt_chars": len(prompt),
            "schema": repo_semantic_fact_output_schema(),
        },
    )

    provider_error = ""
    try:
        raw_response = provider.generate_json(
            prompt,
            schema=repo_semantic_fact_output_schema(),
            max_tokens=max_tokens,
        )
    except ExternalProviderUnavailable as exc:
        provider_error = str(exc)
        raw_response = {"facts": []}
    _write_json(out_dir / "provider_response.json", raw_response)

    parsed = parse_semantic_fact_proposals(raw_response)
    review = review_semantic_fact_proposals(graph=graph, proposals=parsed.proposals)
    report = RepoSemanticProducerEvalReport(
        selected_job_id=str(job_root),
        packet_id=packet.source_id.split(":", 1)[0],
        commit_sha=fixture["packet"]["commit"]["short_sha"],
        provider_model=provider.config.model,
        provider_model_env=provider.config.model_env_used,
        old_accepted_nodes=tuple(old_nodes),
        parse=parsed,
        review=review,
        artifacts_dir=out_dir,
        provider_error=provider_error,
    )
    _write_json(out_dir / "parsed_proposals.json", parsed.as_dict())
    _write_json(out_dir / "review_result.json", review.as_dict())
    _write_json(out_dir / "comparison_report.json", report.as_dict())
    (out_dir / "comparison_report.md").write_text(_render_markdown_report(report), encoding="utf-8")
    return report


def _load_fixture(job_root: Path) -> dict[str, Any]:
    artifacts = {
        "work_packets": _read_json(job_root / "work_packets" / "reasoning_work_packets.json"),
        "qwen_results": _read_json(job_root / "qwen_reasoning" / "results.json"),
        "accepted_reasoning": _read_json(job_root / "reasoning_review" / "accepted_reasoning_nodes.json"),
        "hunks": _read_json(job_root / "git_hunks" / "code_hunks.json"),
        "code_nodes": _read_json(job_root / "ast_code_nodes" / "code_nodes.json"),
        "symbol_versions": _read_json(job_root / "symbol_versions" / "symbol_versions.json"),
        "graph_edges": _read_json(job_root / "reasoning_code_links" / "graph_edges.json"),
    }
    packet = _select_packet(artifacts["work_packets"], artifacts["accepted_reasoning"])
    packet_id = packet["packet_id"]
    return {
        **artifacts,
        "packet": packet,
        "packet_id": packet_id,
        "packet_hunks": [item for item in artifacts["hunks"] if item.get("packet_id") == packet_id],
        "packet_code_nodes": [item for item in artifacts["code_nodes"] if item.get("packet_id") == packet_id],
        "packet_old_nodes": [
            item for item in artifacts["accepted_reasoning"] if item.get("source_packet_id") == packet_id
        ],
    }


def _build_packet_and_graph(
    fixture: dict[str, Any],
) -> tuple[StructuralHarnessGraph, SemanticEvidencePacket, tuple[dict[str, Any], ...]]:
    packet = fixture["packet"]
    repo_id = "repo:production-job:" + _sha256(str(fixture["packet"]["commit"].get("full_sha", "")))[:12]
    nodes, edges = _graph_nodes_and_edges(
        repo_id=repo_id,
        packet=packet,
        hunks=fixture["packet_hunks"],
        code_nodes=fixture["packet_code_nodes"],
        symbol_versions=fixture["symbol_versions"],
    )
    graph = StructuralHarnessGraph(repo_id=repo_id, nodes=tuple(nodes), edges=tuple(edges))
    source_refs = tuple(_source_refs(packet=packet, hunks=fixture["packet_hunks"], old_nodes=fixture["packet_old_nodes"]))
    anchor_ids = tuple(_allowed_anchor_ids(nodes)[:12])
    build = build_semantic_evidence_packet(
        graph,
        source_kind=SOURCE_AGENT_SESSION,
        source_span=SPAN_VALIDATED_COMMITTED,
        source_id=f"{packet['packet_id']}:{packet['commit']['short_sha']}",
        anchor_node_ids=anchor_ids,
        source_refs=source_refs,
        payload=_packet_payload(
            packet=packet,
            nodes=nodes,
            source_refs=source_refs,
            hunks=fixture["packet_hunks"],
            code_nodes=fixture["packet_code_nodes"],
            old_nodes=fixture["packet_old_nodes"],
            qwen_results=fixture["qwen_results"],
        ),
        existing_graph_refs=tuple(node.get("node_id", "") for node in fixture["packet_old_nodes"] if node.get("node_id")),
    )
    if not build.passed or build.packet is None:
        raise ValueError(f"semantic_packet_build_failed:{build.as_dict()}")
    return graph, build.packet, tuple(fixture["packet_old_nodes"])


def _graph_nodes_and_edges(
    *,
    repo_id: str,
    packet: dict[str, Any],
    hunks: list[dict[str, Any]],
    code_nodes: list[dict[str, Any]],
    symbol_versions: dict[str, Any],
) -> tuple[list[HarnessNode], list[HarnessEdge]]:
    nodes: dict[str, HarnessNode] = {}
    edges: list[HarnessEdge] = []
    paths = list(packet.get("commit", {}).get("changed_file_sample") or [])
    paths.extend(item.get("path", "") for item in hunks)
    for path in dict.fromkeys(str(item) for item in paths if str(item)):
        node_id = file_id(repo_id, path)
        nodes[node_id] = HarnessNode(id=node_id, kind="File", label=path, repo_id=repo_id, metadata={"path": path})
    for item in code_nodes[:32]:
        node_id = str(item.get("code_node_id") or "")
        if not node_id:
            continue
        path = str(item.get("path") or "")
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="CodeRegion",
            label=str(item.get("qualified_name") or path or node_id),
            repo_id=repo_id,
            summary=str(item.get("text_excerpt") or ""),
            metadata={
                "path": path,
                "symbol_kind": str(item.get("symbol_kind") or ""),
                "line_start": item.get("line_start", 0),
                "line_end": item.get("line_end", 0),
            },
        )
        file_node_id = file_id(repo_id, path)
        if file_node_id in nodes:
            edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS"))
    symbols = symbol_versions.get("symbols", []) if isinstance(symbol_versions, dict) else []
    for item in symbols:
        if item.get("latest_packet_id") != packet.get("packet_id") and item.get("first_packet_id") != packet.get("packet_id"):
            continue
        node_id = str(item.get("symbol_id") or "")
        if not node_id:
            continue
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="Symbol",
            label=str(item.get("qualified_name") or item.get("symbol_key") or node_id),
            repo_id=repo_id,
            metadata={
                "symbol_key": str(item.get("symbol_key") or ""),
                "symbol_kind": str(item.get("symbol_kind") or ""),
            },
        )
    return list(nodes.values()), edges


def _source_refs(
    *,
    packet: dict[str, Any],
    hunks: list[dict[str, Any]],
    old_nodes: list[dict[str, Any]],
) -> list[SemanticFactSourceRef]:
    refs: list[SemanticFactSourceRef] = []
    for section in ("problem_refs", "rationale_refs", "validation_refs"):
        for item in packet.get(section, [])[:2]:
            refs.append(
                SemanticFactSourceRef(
                    ref_id=str(item.get("ref") or ""),
                    ref_kind=section.rstrip("s"),
                    excerpt=_clip(str(item.get("excerpt") or ""), 180),
                )
            )
    for item in hunks[:4]:
        refs.append(
            SemanticFactSourceRef(
                ref_id=str(item.get("hunk_id") or ""),
                ref_kind="hunk",
                path=str(item.get("path") or ""),
                line=int(item.get("new_start") or 0),
                excerpt=_clip("\n".join(item.get("hunk_lines") or []), 180),
            )
        )
    for item in old_nodes[:2]:
        refs.append(
            SemanticFactSourceRef(
                ref_id=str(item.get("node_id") or ""),
                ref_kind="old_reasoning_node",
                excerpt=_clip(str(item.get("statement") or item.get("reason") or ""), 180),
            )
        )
    return [ref for ref in refs if ref.ref_id and ref.ref_kind]


def _packet_payload(
    *,
    packet: dict[str, Any],
    nodes: list[HarnessNode],
    source_refs: tuple[SemanticFactSourceRef, ...],
    hunks: list[dict[str, Any]],
    code_nodes: list[dict[str, Any]],
    old_nodes: list[dict[str, Any]],
    qwen_results: list[dict[str, Any]],
) -> dict[str, Any]:
    packet_id = packet.get("packet_id")
    return {
        "commit": packet.get("commit", {}),
        "user_problem_refs": _trim_refs(packet.get("problem_refs", [])),
        "agent_rationale_refs": _trim_refs(packet.get("rationale_refs", [])),
        "validation_refs": _trim_refs(packet.get("validation_refs", [])),
        "hunks": [_hunk_summary(item) for item in hunks[:6]],
        "code_nodes": [_code_node_summary(item) for item in code_nodes[:12]],
        "old_qwen_nodes": _old_qwen_nodes(qwen_results, packet_id=packet_id),
        "old_accepted_review_nodes": [
            {
                "node_id": item.get("node_id"),
                "node_type": item.get("node_type"),
                "subject": item.get("subject"),
                "statement": item.get("statement"),
                "reason": item.get("reason"),
                "evidence_refs": item.get("evidence_refs", []),
            }
            for item in old_nodes
        ],
        "allowed_anchor_node_ids": [
            {
                "node_id": node.id,
                "kind": node.kind,
                "label": node.label,
                "summary": _clip(node.summary, 180),
                "metadata": _safe_metadata(node.metadata),
            }
            for node in nodes[:32]
        ],
        "allowed_source_refs": [ref.as_dict() for ref in source_refs],
    }


def _select_packet(work_packets: list[dict[str, Any]], accepted_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    accepted_packet_ids = {str(item.get("source_packet_id") or "") for item in accepted_nodes}
    for packet in work_packets:
        if packet.get("packet_id") in accepted_packet_ids:
            return packet
    if work_packets:
        return work_packets[0]
    raise ValueError("no_work_packets")


def _old_qwen_nodes(qwen_results: list[dict[str, Any]], *, packet_id: str) -> list[dict[str, Any]]:
    for item in qwen_results:
        if item.get("packet_id") != packet_id:
            continue
        parsed = item.get("parsed_output") if isinstance(item.get("parsed_output"), dict) else {}
        nodes = parsed.get("nodes", []) if isinstance(parsed, dict) else []
        return nodes if isinstance(nodes, list) else []
    return []


def _allowed_anchor_ids(nodes: list[HarnessNode]) -> list[str]:
    priority = {"File": 0, "CodeRegion": 1, "Symbol": 2}
    return [node.id for node in sorted(nodes, key=lambda item: (priority.get(item.kind, 9), item.id))]


def _trim_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ref": item.get("ref"),
            "timestamp": item.get("timestamp"),
            "score": item.get("score"),
            "excerpt": _clip(str(item.get("excerpt") or ""), 220),
        }
        for item in refs[:6]
    ]


def _hunk_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "hunk_id": item.get("hunk_id"),
        "path": item.get("path"),
        "new_start": item.get("new_start"),
        "header": item.get("header"),
        "excerpt": _clip("\n".join(item.get("hunk_lines") or []), 220),
    }


def _code_node_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code_node_id": item.get("code_node_id"),
        "path": item.get("path"),
        "symbol_kind": item.get("symbol_kind"),
        "qualified_name": item.get("qualified_name"),
        "line_start": item.get("line_start"),
        "line_end": item.get("line_end"),
        "text_excerpt": _clip(str(item.get("text_excerpt") or ""), 180),
    }


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": metadata.get("path", ""),
        "symbol_kind": metadata.get("symbol_kind", ""),
        "line_start": metadata.get("line_start", 0),
        "line_end": metadata.get("line_end", 0),
    }


def _better_suited_for_context_for_anchor(review: SemanticFactReview) -> bool:
    useful = (*review.accepted_facts, *review.review_only_facts)
    return any(fact.anchor_node_ids and fact.source_refs and not fact.text.lower().startswith("modified ") for fact in useful)


def _coverage(items: Any) -> float:
    rows = list(items)
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if row) / len(rows), 2)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _render_markdown_report(report: RepoSemanticProducerEvalReport) -> str:
    data = report.as_dict()
    summary = data["summary"]
    lines = [
        "# Repo-Semantic Producer Eval",
        "",
        f"- job: `{report.selected_job_id}`",
        f"- packet: `{report.packet_id}`",
        f"- commit: `{report.commit_sha}`",
        f"- provider_model_env: `{report.provider_model_env}`",
        f"- provider_error: `{report.provider_error or 'none'}`",
        "",
        "## Summary",
        "",
        f"- old accepted nodes: {summary['old_accepted_nodes']}",
        f"- new proposed facts: {summary['new_proposed_facts']}",
        f"- accepted/review_only/rejected: {summary['accepted']}/{summary['review_only']}/{summary['rejected']}",
        f"- anchor coverage: {summary['anchor_coverage']}",
        f"- source-ref coverage: {summary['source_ref_coverage']}",
        f"- better suited for context_for_anchor: {summary['better_suited_for_context_for_anchor']}",
        "",
        "## Proposed Facts",
        "",
    ]
    for fact in (*report.review.accepted_facts, *report.review.review_only_facts, *report.review.rejected_facts):
        lines.append(f"- `{fact.review_status}` `{fact.fact_type}` {fact.text}")
    if not report.parse.proposals:
        lines.append("- no proposed facts parsed")
    return "\n".join(lines) + "\n"


__all__ = ["RepoSemanticProducerEvalReport", "run_repo_semantic_producer_eval"]
