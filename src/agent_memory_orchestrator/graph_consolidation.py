from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .graph_store import GraphEdge, GraphNode, GraphStore


CONSOLIDATION_KINDS = {"Decision", "WorkChange", "Fix", "Bug", "Blocker", "TestRun", "ContextSnapshot"}
STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "but",
    "can",
    "did",
    "does",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "not",
    "now",
    "only",
    "that",
    "the",
    "then",
    "this",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
}
CONTRADICT_MARKERS = {"not", "no", "never", "disable", "disabled", "avoid", "stop", "remove", "without"}
AFFIRM_MARKERS = {"use", "enable", "enabled", "allow", "start", "add", "include", "with"}
REFINE_MARKERS = {"add", "extend", "improve", "harden", "clean", "refine", "support", "wire"}
SUPERSEDE_MARKERS = {"instead", "replace", "replaces", "supersede", "supersedes", "now", "new", "migrate"}


@dataclass(slots=True, frozen=True)
class ConsolidationCandidate:
    source_id: str
    target_id: str
    relation: str
    score: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "score": round(self.score, 6),
            "reason": self.reason,
        }


@dataclass(slots=True)
class GraphConsolidationResult:
    scanned_nodes: int
    candidates: list[ConsolidationCandidate] = field(default_factory=list)
    clusters: list[dict[str, Any]] = field(default_factory=list)
    edges_written: int = 0
    clusters_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "scanned_nodes": self.scanned_nodes,
            "candidate_count": len(self.candidates),
            "edges_written": self.edges_written,
            "clusters_written": self.clusters_written,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "clusters": self.clusters,
        }


class DeterministicGraphConsolidator:
    """Local graph consolidation without model calls.

    This is intentionally conservative. It creates provenance-preserving edges;
    it does not overwrite memories or delete older nodes.
    """

    def __init__(self, store: GraphStore, *, project_id: str = "default") -> None:
        self.store = store
        self.project_id = project_id

    def consolidate(self, *, limit: int = 500, apply: bool = False) -> GraphConsolidationResult:
        nodes = [
            node
            for node in self.store.list_nodes(kinds=sorted(CONSOLIDATION_KINDS), limit=max(1, min(5000, int(limit))))
            if _node_text(node)
        ]
        candidates = _classify_pairs(nodes)
        clusters = _clusters_from_candidates(nodes, candidates)
        result = GraphConsolidationResult(scanned_nodes=len(nodes), candidates=candidates[:200], clusters=clusters[:100])
        if apply:
            for candidate in result.candidates:
                self.store.upsert_edge(
                    GraphEdge(
                        id=f"edge:{candidate.source_id}:{candidate.relation}:{candidate.target_id}",
                        source_id=candidate.source_id,
                        target_id=candidate.target_id,
                        kind=candidate.relation,
                        weight=max(0.1, min(1.0, candidate.score)),
                        confidence=max(0.5, min(0.98, candidate.score)),
                        metadata={"reason": candidate.reason, "generated_by": "deterministic_consolidator"},
                    )
                )
                result.edges_written += 1
            for cluster in result.clusters:
                topic_id = f"topic:{_slug(cluster['label'])}:{cluster['id']}"
                self.store.upsert_node(
                    GraphNode(
                        id=topic_id,
                        kind="Topic",
                        label=cluster["label"],
                        summary=f"Consolidated graph cluster: {cluster['label']}",
                        status="active",
                        scope="central",
                        project_id=self.project_id,
                        metadata={
                            "generated_by": "deterministic_consolidator",
                            "member_count": len(cluster["members"]),
                            "members": cluster["members"],
                        },
                    )
                )
                for member_id in cluster["members"]:
                    self.store.upsert_edge(
                        GraphEdge(
                            id=f"edge:{member_id}:MEMBER_OF:{topic_id}",
                            source_id=member_id,
                            target_id=topic_id,
                            kind="MEMBER_OF",
                            weight=0.6,
                            confidence=0.7,
                            metadata={"generated_by": "deterministic_consolidator"},
                        )
                    )
                result.clusters_written += 1
        return result


def _classify_pairs(nodes: list[dict[str, Any]]) -> list[ConsolidationCandidate]:
    rows: list[ConsolidationCandidate] = []
    signatures = {str(node["id"]): _signature(node) for node in nodes}
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            left_id = str(left.get("id") or "")
            right_id = str(right.get("id") or "")
            left_sig = signatures[left_id]
            right_sig = signatures[right_id]
            if not left_sig.tokens or not right_sig.tokens:
                continue
            score = _jaccard(left_sig.tokens, right_sig.tokens)
            if score < 0.38:
                continue
            relation, relation_score, reason = _classify_relation(left, right, left_sig, right_sig, score)
            if not relation:
                continue
            source, target = _orient_relation(left, right, relation)
            rows.append(
                ConsolidationCandidate(
                    source_id=str(source.get("id")),
                    target_id=str(target.get("id")),
                    relation=relation,
                    score=relation_score,
                    reason=reason,
                )
            )
    rows.sort(key=lambda item: (item.score, item.relation), reverse=True)
    return _dedupe_candidates(rows)


def _classify_relation(
    left: dict[str, Any],
    right: dict[str, Any],
    left_sig: "_Signature",
    right_sig: "_Signature",
    overlap: float,
) -> tuple[str, float, str]:
    if left_sig.normalized == right_sig.normalized or overlap >= 0.88:
        return "DUPLICATE_OF", max(overlap, 0.9), f"high lexical overlap {overlap:.2f}"
    if overlap >= 0.52 and _opposes(left_sig, right_sig):
        return "CONTRADICTS", min(0.95, overlap + 0.18), "shared subject with opposing markers"
    if overlap >= 0.48 and (_has_marker(left_sig, SUPERSEDE_MARKERS) or _has_marker(right_sig, SUPERSEDE_MARKERS)):
        return "SUPERSEDES", min(0.92, overlap + 0.22), "shared subject with supersession marker"
    if overlap >= 0.42 and (_has_marker(left_sig, REFINE_MARKERS) or _has_marker(right_sig, REFINE_MARKERS) or _length_ratio(left_sig, right_sig) > 1.35):
        return "REFINES", min(0.9, overlap + 0.18), "shared subject with refinement marker or added detail"
    if overlap >= 0.62:
        return "REFINES", min(0.84, overlap + 0.1), f"moderate lexical overlap {overlap:.2f}"
    return "", 0.0, ""


def _orient_relation(left: dict[str, Any], right: dict[str, Any], relation: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if relation in {"DUPLICATE_OF", "REFINES", "SUPERSEDES"}:
        return (_newer_node(left, right), _older_node(left, right))
    return left, right


def _newer_node(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return left if str(left.get("updated_at") or left.get("created_at") or "") >= str(right.get("updated_at") or right.get("created_at") or "") else right


def _older_node(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    newer = _newer_node(left, right)
    return right if newer is left else left


def _clusters_from_candidates(nodes: list[dict[str, Any]], candidates: list[ConsolidationCandidate]) -> list[dict[str, Any]]:
    parent = {str(node.get("id")): str(node.get("id")) for node in nodes}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for candidate in candidates:
        if candidate.relation in {"DUPLICATE_OF", "REFINES", "SUPERSEDES"} and candidate.score >= 0.55:
            union(candidate.source_id, candidate.target_id)

    by_root: dict[str, list[str]] = {}
    for node_id in parent:
        by_root.setdefault(find(node_id), []).append(node_id)

    node_by_id = {str(node.get("id")): node for node in nodes}
    clusters: list[dict[str, Any]] = []
    for members in by_root.values():
        if len(members) < 2:
            continue
        label = _cluster_label([node_by_id[member] for member in members])
        clusters.append({"id": uuid.uuid5(uuid.NAMESPACE_URL, "|".join(sorted(members))).hex[:12], "label": label, "members": sorted(members)})
    clusters.sort(key=lambda item: len(item["members"]), reverse=True)
    return clusters


@dataclass(slots=True, frozen=True)
class _Signature:
    normalized: str
    tokens: set[str]


def _signature(node: dict[str, Any]) -> _Signature:
    text = _node_text(node).lower()
    tokens = set(_terms(text))
    normalized = " ".join(sorted(tokens))
    return _Signature(normalized=normalized, tokens=tokens)


def _node_text(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    changed_files = metadata.get("changed_files") if isinstance(metadata.get("changed_files"), list) else []
    return " ".join(
        str(part or "")
        for part in (
            node.get("kind"),
            node.get("label"),
            node.get("summary"),
            metadata.get("goal"),
            metadata.get("latest_decision"),
            " ".join(str(item) for item in changed_files),
        )
    ).strip()


def _terms(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_.-]+", text.lower())
        if len(token) > 2 and token not in STOPWORDS and not re.fullmatch(r"[0-9a-f]{7,40}", token)
    ]


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _length_ratio(left: _Signature, right: _Signature) -> float:
    larger = max(len(left.tokens), len(right.tokens))
    smaller = max(1, min(len(left.tokens), len(right.tokens)))
    return larger / smaller


def _opposes(left: _Signature, right: _Signature) -> bool:
    return (_has_marker(left, CONTRADICT_MARKERS) and _has_marker(right, AFFIRM_MARKERS)) or (
        _has_marker(right, CONTRADICT_MARKERS) and _has_marker(left, AFFIRM_MARKERS)
    )


def _has_marker(signature: _Signature, markers: set[str]) -> bool:
    return bool(signature.tokens & markers)


def _dedupe_candidates(candidates: list[ConsolidationCandidate]) -> list[ConsolidationCandidate]:
    seen: set[tuple[str, str, str]] = set()
    rows: list[ConsolidationCandidate] = []
    for candidate in candidates:
        key = (candidate.source_id, candidate.relation, candidate.target_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(candidate)
    return rows


def _cluster_label(nodes: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for node in nodes:
        for term in _terms(_node_text(node)):
            counts[term] = counts.get(term, 0) + 1
    terms = [
        term
        for term, count in sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
        if count > 1
    ][:4]
    if terms:
        return " / ".join(terms)
    kind = str(nodes[0].get("kind") or "Topic")
    return f"{kind} cluster"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "cluster"
