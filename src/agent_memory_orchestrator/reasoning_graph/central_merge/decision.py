from __future__ import annotations

import json
import re
from dataclasses import asdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from .models import ReviewCandidate
from .models import stable_hash


REASONING_EDGE_PREFIX = "REASON_NODE_"

STOPWORDS = {
    "a",
    "about",
    "add",
    "adds",
    "after",
    "an",
    "and",
    "are",
    "as",
    "be",
    "because",
    "before",
    "by",
    "change",
    "commit",
    "decision",
    "did",
    "do",
    "done",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "make",
    "of",
    "on",
    "or",
    "problem",
    "run",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
}


@dataclass(slots=True, frozen=True)
class DecisionFrame:
    frame_id: str
    source_node_id: str
    repo_id: str
    frame_kind: str
    summary: str
    subject: str
    statement: str
    rationale: str
    linked_files: list[str]
    linked_symbols: list[str]
    linked_code_nodes: list[str]
    linked_code_versions: list[str]
    linked_commits: list[str]
    linked_packets: list[str]
    evidence_refs: list[str]
    tokens: list[str]
    graph_neighbor_signature: list[str]
    source_scope: str = "session"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_decision_review_candidates(
    *,
    session_nodes: list[dict[str, Any]] | None = None,
    central_nodes: list[dict[str, Any]] | None = None,
    historical_frames: list[dict[str, Any]] | None = None,
    compact_graph: dict[str, Any] | None = None,
    repo_id: str = "",
    job_id: str = "",
    plan_id: str = "",
) -> dict[str, Any]:
    """Build decision/problem dry-run frames and review candidates.

    This intentionally does not create central decision atoms. It extracts a
    debuggable frame from accepted reasoning nodes and their graph neighbors so
    later phases can judge duplicate/refine/supersede candidates before any
    status mutation is allowed.
    """

    frames = build_decision_frames(compact_graph=compact_graph or {}, session_nodes=session_nodes or [], repo_id=repo_id)
    central_frames = build_decision_frames(session_nodes=_active_central_decision_nodes(central_nodes or [], repo_id=repo_id), repo_id=repo_id)
    persisted_frames = _coerce_historical_frames(historical_frames or [], repo_id=repo_id)
    comparison_frames = [*central_frames, *persisted_frames]
    candidates = _review_candidates(frames=frames, comparison_frames=comparison_frames, job_id=job_id, plan_id=plan_id)
    high_risk = [candidate for candidate in candidates if candidate["score"].get("false_positive_risk")]
    relation_counts: dict[str, int] = {}
    for candidate in candidates:
        relation = str(candidate.get("proposed_relation") or "")
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
    return {
        "frames": [frame.as_dict() for frame in frames],
        "candidates": candidates,
        "metrics": {
            "decision_frame_count": len(frames),
            "active_central_decision_frame_count": len(central_frames),
            "historical_decision_frame_count": len(persisted_frames),
            "decision_candidate_count": len(candidates),
            "review_candidate_count": len(candidates),
            "candidate_relation_counts": relation_counts,
            "false_positive_risk_count": len(high_risk),
            "deferred_central_decision_atom_count": len(frames),
            "note": "Decision/problem matching proposes review relations only; no active decision status is mutated.",
        },
    }


def _coerce_historical_frames(rows: list[dict[str, Any]], *, repo_id: str) -> list[DecisionFrame]:
    frames: list[DecisionFrame] = []
    for row in rows:
        raw = row.get("frame") if isinstance(row.get("frame"), dict) else row
        if not isinstance(raw, dict):
            continue
        if repo_id and str(raw.get("repo_id") or row.get("repo_id") or "") not in {"", repo_id}:
            continue
        try:
            frames.append(
                DecisionFrame(
                    frame_id=str(raw.get("frame_id") or row.get("frame_id") or ""),
                    source_node_id=str(raw.get("source_node_id") or row.get("source_node_id") or raw.get("frame_id") or ""),
                    repo_id=str(raw.get("repo_id") or row.get("repo_id") or repo_id),
                    frame_kind=str(raw.get("frame_kind") or row.get("frame_kind") or "decision"),
                    summary=str(raw.get("summary") or row.get("summary") or ""),
                    subject=str(raw.get("subject") or row.get("subject") or ""),
                    statement=str(raw.get("statement") or row.get("statement") or ""),
                    rationale=str(raw.get("rationale") or ""),
                    linked_files=_list(raw.get("linked_files")),
                    linked_symbols=_list(raw.get("linked_symbols")),
                    linked_code_nodes=_list(raw.get("linked_code_nodes")),
                    linked_code_versions=_list(raw.get("linked_code_versions")),
                    linked_commits=_list(raw.get("linked_commits")),
                    linked_packets=_list(raw.get("linked_packets")),
                    evidence_refs=_list(raw.get("evidence_refs")),
                    tokens=_list(raw.get("tokens")),
                    graph_neighbor_signature=_list(raw.get("graph_neighbor_signature")),
                    source_scope="decision_frame_ledger",
                )
            )
        except TypeError:
            continue
    return [frame for frame in frames if frame.frame_id and frame.source_node_id]


def build_decision_frames(
    *,
    compact_graph: dict[str, Any] | None = None,
    session_nodes: list[dict[str, Any]] | None = None,
    repo_id: str = "",
) -> list[DecisionFrame]:
    nodes = _nodes(compact_graph) if compact_graph else list(session_nodes or [])
    if not nodes:
        return []
    edges = _edges(compact_graph) if compact_graph else []
    node_by_id = {_node_id(node): node for node in nodes if _node_id(node)}
    edges_by_node = _edges_by_node(edges)
    frames: list[DecisionFrame] = []
    for node in nodes:
        if not _is_decision_like(node):
            continue
        node_id = _node_id(node)
        props = _properties(node)
        summary = _first(props, "statement", "summary", "label")
        subject = _first(props, "subject", "title", "label")
        statement = _first(props, "statement", "summary")
        rationale = _first(props, "reason", "rationale", "why")
        edge_context = _edge_context(
            node_id=node_id,
            edges=edges_by_node.get(node_id, []),
            node_by_id=node_by_id,
            edges_by_node=edges_by_node,
        )
        linked_packets = _dedupe([_first(props, "source_packet_id", "packet_id"), *_list(props.get("linked_packets")), *edge_context["packets"]])
        linked_commits = _dedupe([_first(props, "source_commit_sha", "commit_sha"), *_list(props.get("linked_commits")), *edge_context["commits"]])
        evidence_refs = _dedupe([*_list(props.get("evidence_refs")), *edge_context["evidence_refs"]])
        linked_files = _dedupe(
            [*_list(props.get("selected_files")), *_list(props.get("linked_files")), _first(props, "path", "file_path", "normalized_file_path"), *edge_context["files"]]
        )
        linked_symbols = _dedupe([*_list(props.get("selected_symbol_refs")), *_list(props.get("linked_symbols")), *edge_context["symbols"]])
        text_for_tokens = " ".join([summary, subject, statement, rationale])
        tokens = _tokens(text_for_tokens)
        signature = _dedupe(edge_context["neighbor_signature"])
        frame_seed = {"repo_id": repo_id, "source_node_id": node_id, "summary": summary}
        frames.append(
            DecisionFrame(
                frame_id=f"dframe:{stable_hash(frame_seed)[:24]}",
                source_node_id=node_id,
                repo_id=repo_id,
                frame_kind=_frame_kind(props, summary),
                summary=summary,
                subject=subject,
                statement=statement,
                rationale=rationale,
                linked_files=linked_files,
                linked_symbols=linked_symbols,
                linked_code_nodes=_dedupe(edge_context["code_nodes"]),
                linked_code_versions=_dedupe(edge_context["code_versions"]),
                linked_commits=linked_commits,
                linked_packets=linked_packets,
                evidence_refs=evidence_refs,
                tokens=tokens,
                graph_neighbor_signature=signature,
                source_scope=_frame_source_scope(node),
            )
        )
    return frames


def _review_candidates(
    *,
    frames: list[DecisionFrame],
    job_id: str,
    plan_id: str,
    comparison_frames: list[DecisionFrame] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    pairs = list(combinations(frames, 2))
    if comparison_frames:
        pairs.extend((left, right) for left in frames for right in comparison_frames)
    for left, right in pairs:
        if left.source_node_id == right.source_node_id:
            continue
        score = _pair_score(left, right)
        if score["total"] < 0.52 and not _is_text_only_review(score) and not _is_shared_code_review(score):
            continue
        relation, reason = _relation_for_score(score, left=left, right=right)
        if relation == "":
            continue
        source_frame, target_frame = _orient_candidate(left, right, relation)
        candidate_seed = {
            "plan_id": plan_id,
            "source": source_frame.source_node_id,
            "target": target_frame.source_node_id,
            "relation": relation,
        }
        candidates.append(
            ReviewCandidate(
                candidate_id=f"v2review:{stable_hash(candidate_seed)[:32]}",
                plan_id=plan_id,
                job_id=job_id,
                source_node_id=source_frame.source_node_id,
                target_node_id=target_frame.source_node_id,
                proposed_relation=relation,
                score={
                    **score,
                    "source_summary": source_frame.summary,
                    "target_summary": target_frame.summary,
                    "source_files": source_frame.linked_files[:12],
                    "target_files": target_frame.linked_files[:12],
                    "source_symbols": source_frame.linked_symbols[:12],
                    "target_symbols": target_frame.linked_symbols[:12],
                    "source_scope": source_frame.source_scope,
                    "target_scope": target_frame.source_scope,
                    "source_frame_id": source_frame.frame_id,
                    "target_frame_id": target_frame.frame_id,
                    "source_kind": source_frame.frame_kind,
                    "target_kind": target_frame.frame_kind,
                },
                reason=reason,
            ).as_dict()
        )
    return candidates


def _pair_score(left: DecisionFrame, right: DecisionFrame) -> dict[str, Any]:
    lexical = _jaccard(left.tokens, right.tokens)
    file_overlap = _jaccard(left.linked_files, right.linked_files)
    symbol_overlap = _jaccard(left.linked_symbols, right.linked_symbols)
    code_overlap = max(file_overlap, symbol_overlap)
    graph_overlap = _jaccard(left.graph_neighbor_signature, right.graph_neighbor_signature)
    evidence_overlap = _jaccard(left.evidence_refs, right.evidence_refs)
    commit_overlap = _jaccard(left.linked_commits, right.linked_commits)
    total = (0.45 * lexical) + (0.35 * code_overlap) + (0.15 * graph_overlap) + (0.05 * max(evidence_overlap, commit_overlap))
    return {
        "total": round(total, 4),
        "lexical": round(lexical, 4),
        "code_entity_overlap": round(code_overlap, 4),
        "file_overlap": round(file_overlap, 4),
        "symbol_overlap": round(symbol_overlap, 4),
        "graph_neighbor_overlap": round(graph_overlap, 4),
        "evidence_overlap": round(evidence_overlap, 4),
        "commit_overlap": round(commit_overlap, 4),
        "false_positive_risk": lexical >= 0.25 and code_overlap < 0.15,
    }


def _relation_for_score(score: dict[str, Any], *, left: DecisionFrame, right: DecisionFrame) -> tuple[str, str]:
    total = float(score["total"])
    code_overlap = float(score["code_entity_overlap"])
    lexical = float(score["lexical"])
    if (_supersedes(left) or _supersedes(right)) and total >= 0.48 and (code_overlap >= 0.2 or lexical >= 0.45):
        return "SUPERSEDES", "new_decision_uses_replacement_language"
    if _conflicts(left, right) and total >= 0.48 and (code_overlap >= 0.2 or lexical >= 0.5):
        return "CONFLICTS_WITH", "incompatible_decision_language"
    if lexical >= 0.82 and code_overlap >= 0.5:
        return "DUPLICATE_OF", "same_decision_text_and_code_context"
    if total >= 0.88 and code_overlap >= 0.35:
        return "DUPLICATE_OF", "high_overlap_same_code_context"
    if total >= 0.76 and code_overlap >= 0.25:
        return "REFINES", "high_overlap_possible_refinement"
    if total >= 0.52 and (code_overlap >= 0.15 or lexical >= 0.45):
        return "RELATED_REVIEW", "related_decision_needs_human_review"
    if _is_text_only_review(score):
        return "RELATED_REVIEW", "text_overlap_without_shared_code_context"
    if _is_shared_code_review(score):
        return "RELATED_REVIEW", "shared_code_context_needs_human_review"
    return "", ""


def _orient_candidate(left: DecisionFrame, right: DecisionFrame, relation: str) -> tuple[DecisionFrame, DecisionFrame]:
    if relation == "SUPERSEDES":
        if _supersedes(right) and not _supersedes(left):
            return right, left
        return left, right
    if relation == "REFINES":
        left_len = len(left.tokens) + len(left.linked_files) + len(left.linked_symbols)
        right_len = len(right.tokens) + len(right.linked_files) + len(right.linked_symbols)
        return (left, right) if left_len >= right_len else (right, left)
    return left, right


def _supersedes(frame: DecisionFrame) -> bool:
    text = _frame_text(frame)
    return any(
        marker in text
        for marker in (
            "replace",
            "replaces",
            "replaced",
            "supersede",
            "supersedes",
            "instead of",
            "migrate from",
            "migrates from",
            "move from",
            "moves from",
            "switch from",
            "switches from",
            "no longer",
            "stop using",
            "remove old",
        )
    )


def _conflicts(left: DecisionFrame, right: DecisionFrame) -> bool:
    left_text = _frame_text(left)
    right_text = _frame_text(right)
    oppositions = (
        ("enable", "disable"),
        ("enabled", "disabled"),
        ("allow", "block"),
        ("remote", "local"),
        ("sync", "async"),
        ("strict", "permissive"),
        ("required", "optional"),
        ("central", "session"),
        ("active", "inactive"),
    )
    return any((a in left_text and b in right_text) or (b in left_text and a in right_text) for a, b in oppositions)


def _frame_text(frame: DecisionFrame) -> str:
    return " ".join([frame.subject, frame.summary, frame.statement, frame.rationale]).lower()


def _is_text_only_review(score: dict[str, Any]) -> bool:
    return float(score["lexical"]) >= 0.25 and float(score["code_entity_overlap"]) < 0.15


def _is_shared_code_review(score: dict[str, Any]) -> bool:
    return float(score["code_entity_overlap"]) >= 0.6 and float(score["graph_neighbor_overlap"]) >= 0.25


def _edge_context(
    *,
    node_id: str,
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    edges_by_node: dict[str, list[dict[str, Any]]],
) -> dict[str, list[str]]:
    context: dict[str, list[str]] = {
        "packets": [],
        "commits": [],
        "evidence_refs": [],
        "files": [],
        "symbols": [],
        "code_nodes": [],
        "code_versions": [],
        "neighbor_signature": [],
    }
    for edge in edges:
        kind = _edge_kind(edge)
        other_id = _other_node_id(edge, node_id)
        other = node_by_id.get(other_id, {})
        if not kind.startswith(REASONING_EDGE_PREFIX):
            continue
        context["neighbor_signature"].append(f"{kind}:{other_id}")
        if kind == "REASON_NODE_IN_PACKET":
            context["packets"].append(other_id)
        elif kind == "REASON_NODE_EXPLAINS_COMMIT":
            context["commits"].append(_strip_prefix(other_id, "commit:"))
        elif kind == "REASON_NODE_EVIDENCED_BY":
            context["evidence_refs"].append(other_id)
        elif kind == "REASON_NODE_LINKED_TO_CODE_NODE":
            context["code_nodes"].append(other_id)
            context["files"].extend(_file_refs(other))
        elif kind == "REASON_NODE_LINKED_TO_CODE_VERSION":
            context["code_versions"].append(other_id)
            context["files"].extend(_file_refs(other))
        elif kind == "REASON_NODE_LINKED_TO_SYMBOL":
            context["symbols"].append(_symbol_ref(other, other_id))
            context["files"].extend(_file_refs(other))
        elif kind == "REASON_NODE_LINKED_TO_HUNK":
            context["files"].extend(_file_refs(other))
        elif kind == "REASON_NODE_HAS_CODE_IMPACT":
            _merge_context(context, _code_impact_context(other_id, other, node_by_id=node_by_id, edges_by_node=edges_by_node))
    return context


def _code_impact_context(
    impact_id: str,
    impact_node: dict[str, Any],
    *,
    node_by_id: dict[str, dict[str, Any]],
    edges_by_node: dict[str, list[dict[str, Any]]],
) -> dict[str, list[str]]:
    context: dict[str, list[str]] = {
        "packets": [],
        "commits": [],
        "evidence_refs": [],
        "files": [],
        "symbols": [],
        "code_nodes": [],
        "code_versions": [],
        "neighbor_signature": [],
    }
    props = _properties(impact_node)
    context["packets"].extend(_list(props.get("packet_id")))
    context["commits"].extend(_list(props.get("commit_sha")))
    context["files"].extend(_normalize_path(path) for path in _list(props.get("selected_files")))
    for symbol_id in _list(props.get("selected_symbol_refs")):
        symbol = node_by_id.get(symbol_id, {})
        context["symbols"].append(_symbol_ref(symbol, symbol_id))
        context["files"].extend(_file_refs(symbol))
        context["neighbor_signature"].append(f"CODE_IMPACT_TOUCHES_SYMBOL:{symbol_id}")
    for code_id in _list(props.get("selected_code_refs")):
        code = node_by_id.get(code_id, {})
        context["code_nodes"].append(code_id)
        context["files"].extend(_file_refs(code))
        context["neighbor_signature"].append(f"CODE_IMPACT_TOUCHES_CODE_REGION:{code_id}")
    for edge in edges_by_node.get(impact_id, []):
        kind = _edge_kind(edge)
        other_id = _other_node_id(edge, impact_id)
        other = node_by_id.get(other_id, {})
        if kind == "CODE_IMPACT_TOUCHES_FILE":
            context["files"].extend(_file_refs(other))
        elif kind == "CODE_IMPACT_TOUCHES_SYMBOL":
            context["symbols"].append(_symbol_ref(other, other_id))
            context["files"].extend(_file_refs(other))
        elif kind == "CODE_IMPACT_TOUCHES_CODE_REGION":
            context["code_nodes"].append(other_id)
            context["files"].extend(_file_refs(other))
        elif kind == "CODE_IMPACT_IMPLEMENTED_BY_COMMIT":
            context["commits"].append(_strip_prefix(other_id, "commit:"))
        if kind.startswith("CODE_IMPACT_"):
            context["neighbor_signature"].append(f"{kind}:{other_id}")
    return context


def _merge_context(target: dict[str, list[str]], source: dict[str, list[str]]) -> None:
    for key, values in source.items():
        target.setdefault(key, []).extend(values)


def _nodes(compact_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    nodes = compact_graph.get("nodes") if isinstance(compact_graph, dict) else []
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _edges(compact_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    edges = compact_graph.get("edges") if isinstance(compact_graph, dict) else []
    return [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []


def _edges_by_node(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        source = _edge_source(edge)
        target = _edge_target(edge)
        if source:
            rows.setdefault(source, []).append(edge)
        if target and target != source:
            rows.setdefault(target, []).append(edge)
    return rows


def _is_decision_like(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or node.get("node_kind") or "").lower()
    if kind in {"decision", "problem"}:
        return True
    props = _properties(node)
    if kind == "knowledgeversion":
        atom_kind = str(props.get("atom_kind") or "").lower()
        status = str(props.get("status") or node.get("status") or "").lower()
        return atom_kind in {"decision", "problem"} and status in {"", "active", "review"}
    if kind != "reasoningnode":
        return False
    node_type = str(props.get("node_type") or "").lower()
    status = str(props.get("status") or "").lower()
    text = " ".join([_first(props, "label"), _first(props, "summary"), _first(props, "statement")]).lower()
    return status in {"", "accepted"} and (node_type in {"decision", "problem"} or "decision:" in text or "problem:" in text)


def _frame_kind(props: dict[str, Any], summary: str) -> str:
    node_type = str(props.get("node_type") or "").strip().lower()
    if node_type in {"decision", "problem"}:
        return node_type
    lower = summary.lower()
    if lower.startswith("problem:") or "problem with" in lower:
        return "problem"
    return "decision"


def _properties(node: dict[str, Any]) -> dict[str, Any]:
    raw: dict[str, Any] = dict(node)
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    raw.update(props)
    top_metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    raw.update(top_metadata)
    metadata = props.get("metadata") if isinstance(props.get("metadata"), dict) else {}
    raw.update(metadata)
    version_metadata = raw.get("version_metadata") if isinstance(raw.get("version_metadata"), dict) else {}
    raw.update(version_metadata)
    encoded = raw.get("properties_json")
    if isinstance(encoded, str) and encoded.strip():
        try:
            decoded = json.loads(encoded)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            raw.update(decoded)
    return raw


def _active_central_decision_nodes(nodes: list[dict[str, Any]], *, repo_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        props = _properties(node)
        if repo_id and str(props.get("repo_id") or "") not in {"", repo_id}:
            continue
        if str(node.get("kind") or node.get("node_kind") or "").lower() != "knowledgeversion":
            continue
        if str(props.get("status") or node.get("status") or "").lower() not in {"", "active", "review"}:
            continue
        if str(props.get("atom_kind") or "").lower() in {"decision", "problem"}:
            out.append(node)
    return out


def _frame_source_scope(node: dict[str, Any]) -> str:
    return "central" if str(node.get("kind") or node.get("node_kind") or "").lower() == "knowledgeversion" else "session"


def _file_refs(node: dict[str, Any]) -> list[str]:
    props = _properties(node)
    refs = [
        _first(props, "normalized_file_path", "file_path", "path"),
        _file_from_label(_first(props, "label", "summary", "name")),
    ]
    return [_normalize_path(ref) for ref in refs if _normalize_path(ref)]


def _symbol_ref(node: dict[str, Any], fallback_id: str) -> str:
    props = _properties(node)
    file_path = _normalize_path(_first(props, "normalized_file_path", "file_path", "path") or _file_from_label(_first(props, "label", "summary", "name")))
    name = _first(props, "qualified_name", "symbol_name", "name", "structural_id", "label")
    if file_path and name:
        return f"{file_path}::{name}"
    return fallback_id


def _file_from_label(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    candidate = text.split("::", 1)[0].strip()
    candidate = re.sub(r":\d+(?::\d+)?$", "", candidate).strip()
    if "/" not in candidate and "\\" not in candidate:
        return ""
    return candidate


def _tokens(text: str) -> list[str]:
    found = re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]{2,}", text.lower())
    return sorted({token for token in found if token not in STOPWORDS and len(token) > 2})


def _jaccard(left: list[str], right: list[str]) -> float:
    left_set = {item for item in left if item}
    right_set = {item for item in right if item}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _edge_source(edge: dict[str, Any]) -> str:
    return str(edge.get("from_id") or edge.get("source_id") or edge.get("source") or "")


def _edge_target(edge: dict[str, Any]) -> str:
    return str(edge.get("to_id") or edge.get("target_id") or edge.get("target") or "")


def _edge_kind(edge: dict[str, Any]) -> str:
    return str(edge.get("kind") or edge.get("edge_kind") or "")


def _other_node_id(edge: dict[str, Any], node_id: str) -> str:
    source = _edge_source(edge)
    target = _edge_target(edge)
    if source == node_id:
        return target
    if target == node_id:
        return source
    return target or source


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or node.get("node_id") or node.get("key") or "")


def _first(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if value is None or not str(value).strip():
        return []
    return [str(value).strip()]


def _dedupe(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def _normalize_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _strip_prefix(value: str, prefix: str) -> str:
    text = str(value or "")
    return text[len(prefix) :] if text.startswith(prefix) else text
