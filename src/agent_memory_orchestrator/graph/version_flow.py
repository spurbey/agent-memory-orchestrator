from __future__ import annotations

from typing import Any

from .constants import ANSWER_SEED_KINDS
from .constants import ISOLATED_GRAPH_VISUAL_KINDS
from .constants import ISOLATED_GRAPH_VISUAL_STATUSES
from .constants import VERSION_FLOW_EDGE_KINDS
from .constants import VERSION_RELATION_EDGE_KINDS
from .store import GraphStore
from .text_utils import _clip


def _matches_version_flow_filter(node: dict[str, Any], *, commit: str, session_id: str) -> bool:
    if session_id and str(node.get("session_id") or "") != session_id:
        return False
    if commit and commit.upper() != "HEAD" and not _matches_commit(node, commit):
        return False
    return True


def _matches_commit(node: dict[str, Any], commit: str) -> bool:
    needle = str(commit or "").strip().lower()
    if not needle:
        return True
    values = [
        str(node.get("id") or ""),
        str(node.get("label") or ""),
        str(node.get("commit_id") or ""),
    ]
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    commit_meta = meta.get("commit") if isinstance(meta.get("commit"), dict) else {}
    values.append(str(commit_meta.get("commit") or ""))
    return any(value.lower().startswith(needle) or needle in value.lower() for value in values if value)


def _is_central_graph_seed(node: dict[str, Any]) -> bool:
    return str(node.get("scope") or "") == "central" or str(node.get("status") or "") in {"committed", "active"}


def _is_isolated_graph_seed(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or "")
    status = str(node.get("status") or "")
    if kind not in ISOLATED_GRAPH_VISUAL_KINDS:
        return False
    if status in ISOLATED_GRAPH_VISUAL_STATUSES:
        return True
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(metadata.get("stage") or "").startswith("stage")


def _isolated_graph_seed_pool(
    store: GraphStore,
    nodes: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    priority = {
        "ReasoningNode": 0,
        "Decision": 1,
        "Problem": 1,
        "Cause": 1,
        "Fix": 1,
        "Constraint": 1,
        "Commit": 2,
        "GitCommit": 2,
        "Packet": 3,
        "CodeNode": 4,
        "Symbol": 5,
        "CodeVersion": 6,
        "CodeHunk": 7,
        "EvidenceRef": 8,
    }
    rows = list(nodes)
    per_kind_limit = limit if limit > 500 else max(20, min(160, limit))
    for kind in priority:
        rows.extend(store.list_nodes(kinds=[kind], limit=per_kind_limit))
    unique: dict[str, dict[str, Any]] = {}
    for node in rows:
        node_id = str(node.get("id") or "")
        if node_id and node_id not in unique:
            unique[node_id] = node
    return sorted(
        unique.values(),
        key=lambda node: (
            priority.get(str(node.get("kind") or ""), 50),
            str(node.get("commit_id") or ""),
            str(node.get("id") or ""),
        ),
    )


def _build_version_flow(
    *,
    commit_node: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    commit_node_id = str(commit_node.get("id") or "")
    commit_id = str(commit_node.get("commit_id") or "")
    related_edges = [
        edge
        for edge in edges
        if edge.get("kind") in VERSION_FLOW_EDGE_KINDS
        and _edge_mentions_commit(edge, commit_node_id=commit_node_id, commit_id=commit_id)
    ]
    work_ids = {
        str(edge.get("source_id") or "")
        for edge in related_edges
        if edge.get("kind") == "COMMITTED_AS" and str(edge.get("target_id") or "") == commit_node_id
    }
    work_ids.update(
        str(node.get("id") or "")
        for node in nodes
        if str(node.get("commit_id") or "") == commit_id and str(node.get("kind") or "") in ANSWER_SEED_KINDS
    )
    work_ids.discard(commit_node_id)

    flow_edge_ids = {str(edge.get("id") or "") for edge in related_edges}
    frontier = set(work_ids) | {commit_node_id}
    for edge in edges:
        kind = str(edge.get("kind") or "")
        source = str(edge.get("source_id") or "")
        target = str(edge.get("target_id") or "")
        if kind not in VERSION_FLOW_EDGE_KINDS:
            continue
        if source in frontier or target in frontier:
            flow_edge_ids.add(str(edge.get("id") or ""))
            if kind in {"MODIFIES", "VALIDATED_BY", *VERSION_RELATION_EDGE_KINDS, "MERGED_INTO"}:
                frontier.update([source, target])

    flow_edges = [edge for edge in edges if str(edge.get("id") or "") in flow_edge_ids]
    flow_node_ids = {commit_node_id}
    for edge in flow_edges:
        flow_node_ids.add(str(edge.get("source_id") or ""))
        flow_node_ids.add(str(edge.get("target_id") or ""))
    flow_node_ids.update(work_ids)
    flow_nodes = [node_by_id[node_id] for node_id in flow_node_ids if node_id in node_by_id]
    work_nodes = [node_by_id[node_id] for node_id in work_ids if node_id in node_by_id]
    files = _flow_nodes_for_edges(flow_edges, node_by_id, kind="MODIFIES", endpoint="target")
    tests = _flow_nodes_for_edges(flow_edges, node_by_id, kind="VALIDATED_BY", endpoint="source")
    evidence_ids = sorted(
        {
            str(value)
            for value in [
                commit_node.get("evidence_id"),
                *(node.get("evidence_id") for node in work_nodes),
                *(edge.get("evidence_id") for edge in flow_edges),
            ]
            if value
        }
    )
    version_edges = [
        edge
        for edge in flow_edges
        if _is_version_relation_edge(edge) and _has_durable_relation_endpoints(edge, node_by_id)
    ]
    return {
        "commit_node": commit_node,
        "commit_id": commit_id,
        "summary": _version_flow_summary(commit_node, work_nodes, files),
        "counts": {
            "work_nodes": len(work_nodes),
            "files": len(files),
            "tests": len(tests),
            "version_edges": len(version_edges),
            "evidence_refs": len(evidence_ids),
        },
        "work_nodes": work_nodes,
        "files": files,
        "tests": tests,
        "evidence_ids": evidence_ids,
        "version_edges": version_edges,
        "edges": flow_edges,
        "nodes": flow_nodes,
    }


def _build_central_version_flows(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    commit: str,
    session_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    graph_commits = [
        node
        for node in nodes
        if str(node.get("kind") or "") == "GraphCommit"
        and str(node.get("status") or "") == "applied"
        and (not session_id or str(node.get("session_id") or _node_metadata(node).get("session_id") or "") == session_id)
    ]
    graph_commits.sort(key=lambda node: str(node.get("created_at") or node.get("updated_at") or ""), reverse=True)
    flows: list[dict[str, Any]] = []
    for graph_commit in graph_commits:
        graph_commit_id = str(graph_commit.get("id") or "")
        versions = [
            node
            for node in nodes
            if str(node.get("kind") or "") == "KnowledgeVersion"
            and str(_node_metadata(node).get("graph_commit_id") or "") == graph_commit_id
        ]
        if commit and commit.upper() != "HEAD" and not any(_central_version_matches_commit(node, commit) for node in versions):
            continue
        if not versions:
            continue
        version_ids = {str(node.get("id") or "") for node in versions}
        version_edges = [
            edge
            for edge in edges
            if str(edge.get("kind") or "") == "VERSION_OF" and str(edge.get("source_id") or "") in version_ids
        ]
        atoms = [
            node_by_id[str(edge.get("target_id") or "")]
            for edge in version_edges
            if str(edge.get("target_id") or "") in node_by_id
        ]
        commit_versions = [node for node in versions if _central_version_atom_kind(node) == "commit"]
        file_versions = [node for node in versions if _central_version_atom_kind(node) == "file"]
        commit_ids = sorted({_central_version_commit_id(node) for node in commit_versions if _central_version_commit_id(node)})
        file_paths = sorted({_central_version_file_path(node) for node in file_versions if _central_version_file_path(node)})
        flow = {
            "flow_type": "central_version",
            "graph_commit_id": graph_commit_id,
            "parent_graph_commit_id": str(_node_metadata(graph_commit).get("parent_graph_commit_id") or ""),
            "commit_id": commit_ids[0] if len(commit_ids) == 1 else "",
            "commit_ids": commit_ids,
            "session_id": str(graph_commit.get("session_id") or _node_metadata(graph_commit).get("session_id") or ""),
            "job_id": str(_node_metadata(graph_commit).get("job_id") or ""),
            "plan_id": str(_node_metadata(graph_commit).get("merge_plan_id") or ""),
            "versions": versions,
            "commit_versions": commit_versions,
            "file_versions": file_versions,
            "files": file_paths,
            "nodes": [graph_commit, *versions, *atoms],
            "edges": version_edges,
            "evidence_ids": [],
            "counts": {
                "work_nodes": len(versions),
                "commit_versions": len(commit_versions),
                "file_versions": len(file_versions),
                "version_edges": len(version_edges),
            },
            "summary": _central_version_flow_summary(graph_commit, commit_ids, file_paths),
        }
        flows.append(flow)
        if len(flows) >= limit:
            break
    return flows


def _node_metadata(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _central_version_atom_kind(node: dict[str, Any]) -> str:
    return str(_node_metadata(node).get("atom_kind") or "")


def _central_version_matches_commit(node: dict[str, Any], commit: str) -> bool:
    needle = str(commit or "").strip().lower()
    if not needle:
        return True
    metadata = _node_metadata(node)
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    source_node_ids = metadata.get("source_node_ids") if isinstance(metadata.get("source_node_ids"), list) else []
    values = [
        str(node.get("id") or ""),
        str(node.get("label") or ""),
        str(node.get("summary") or ""),
        str(version_metadata.get("canonical_key") or ""),
        " ".join(str(value) for value in source_node_ids if value),
    ]
    return any(needle in value.lower() for value in values if value)


def _central_version_commit_id(node: dict[str, Any]) -> str:
    metadata = _node_metadata(node)
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    canonical_key = str(version_metadata.get("canonical_key") or node.get("label") or "")
    parts = canonical_key.split("|")
    if len(parts) >= 3 and parts[0] == "commit":
        return parts[-1]
    source_node_ids = metadata.get("source_node_ids") if isinstance(metadata.get("source_node_ids"), list) else []
    for source_id in source_node_ids:
        if str(source_id).startswith("commit:"):
            return str(source_id).split(":", 1)[1]
    return ""


def _central_version_file_path(node: dict[str, Any]) -> str:
    metadata = _node_metadata(node)
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    canonical_key = str(version_metadata.get("canonical_key") or node.get("label") or "")
    parts = canonical_key.split("|")
    if len(parts) >= 4 and parts[0] == "file":
        return "|".join(parts[2:-1])
    if len(parts) >= 3 and parts[0] == "file":
        return parts[-1]
    return ""


def _central_version_flow_summary(graph_commit: dict[str, Any], commit_ids: list[str], file_paths: list[str]) -> str:
    commit_text = ", ".join(commit_id[:12] for commit_id in commit_ids[:4]) or "no commit versions"
    file_text = ", ".join(file_paths[:5]) or "no file versions"
    suffix = "" if len(file_paths) <= 5 else f", +{len(file_paths) - 5} more files"
    return _clip(f"{graph_commit.get('id')} applied commit/file versions: commits {commit_text}; files {file_text}{suffix}", 520)


def _edge_mentions_commit(edge: dict[str, Any], *, commit_node_id: str, commit_id: str) -> bool:
    if str(edge.get("source_id") or "") == commit_node_id or str(edge.get("target_id") or "") == commit_node_id:
        return True
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    edge_commit = str(metadata.get("commit_id") or metadata.get("commit") or "")
    return bool(commit_id and edge_commit == commit_id)


def _flow_nodes_for_edges(
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    *,
    kind: str,
    endpoint: str,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for edge in edges:
        if str(edge.get("kind") or "") != kind:
            continue
        node_id = str(edge.get("target_id" if endpoint == "target" else "source_id") or "")
        if node_id in seen or node_id not in node_by_id:
            continue
        seen.add(node_id)
        rows.append(node_by_id[node_id])
    return rows


def _is_version_relation_edge(edge: dict[str, Any]) -> bool:
    kind = str(edge.get("kind") or "")
    if kind in {"DUPLICATE_OF", "SUPERSEDES", "CONTRADICTS"}:
        return True
    if kind != "REFINES":
        return False
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    return any(key in metadata for key in ("reason", "source", "score", "commit_id"))


def _has_durable_relation_endpoints(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> bool:
    source = node_by_id.get(str(edge.get("source_id") or ""))
    target = node_by_id.get(str(edge.get("target_id") or ""))
    if not source or not target:
        return False
    return all(
        str(node.get("status") or "") in {"committed", "active", "superseded"}
        or str(node.get("scope") or "") == "central"
        for node in (source, target)
    )


def _version_flow_summary(commit_node: dict[str, Any], work_nodes: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    subject = str(commit_node.get("summary") or commit_node.get("label") or "Commit")
    work = "; ".join(_clip(node.get("summary") or node.get("label"), 90) for node in work_nodes[:3] if node)
    file_text = ", ".join(str(node.get("label") or "") for node in files[:5] if node.get("label"))
    parts = [subject]
    if work:
        parts.append(f"promoted: {work}")
    if file_text:
        parts.append(f"files: {file_text}")
    return _clip(" | ".join(parts), 520)


def _version_flow_warnings(flows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not flows:
        warnings.append("no_committed_version_flows_found")
        return warnings
    if not any(flow.get("counts", {}).get("work_nodes") for flow in flows):
        warnings.append("version_flows_have_no_promoted_work_nodes")
    if not any(flow.get("counts", {}).get("version_edges") for flow in flows):
        warnings.append("version_flows_have_no_refine_supersede_duplicate_edges")
    return warnings

