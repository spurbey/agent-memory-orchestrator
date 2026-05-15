from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class CompactSessionGraph:
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    unresolved_edges: tuple[dict[str, Any], ...]
    inventory: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "unresolved_edges": list(self.unresolved_edges),
            "inventory": self.inventory,
        }


@dataclass(slots=True, frozen=True)
class CompactKuzuWriteResult:
    ok: bool
    graph_path: str
    node_count: int
    edge_count: int
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "graph_path": self.graph_path,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "diagnostics": list(self.diagnostics),
        }


def build_compact_session_graph(
    *,
    packets: list[dict[str, Any]],
    reasoning_nodes: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    commit_nodes: list[dict[str, Any]],
    code_hunks: list[dict[str, Any]],
    code_nodes: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    code_versions: list[dict[str, Any]],
    raw_edges: list[dict[str, Any]],
) -> CompactSessionGraph:
    nodes: list[dict[str, Any]] = []
    nodes.extend(_packet_nodes(packets))
    nodes.extend(_commit_nodes(commit_nodes))
    nodes.extend(_evidence_ref_nodes(evidence_refs))
    nodes.extend(_reasoning_nodes(reasoning_nodes))
    nodes.extend(_code_hunk_nodes(code_hunks))
    nodes.extend(_code_nodes(code_nodes))
    nodes.extend(_symbol_nodes(symbols))
    nodes.extend(_code_version_nodes(code_versions))

    deduped_nodes = _dedupe_nodes(nodes)
    node_ids = {node["id"] for node in deduped_nodes}
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for raw_edge in raw_edges:
        normalized = _edge(raw_edge)
        if not normalized.get("from_id") or not normalized.get("to_id"):
            unresolved.append({**normalized, "reason": "missing_endpoint"})
            continue
        if normalized["from_id"] not in node_ids or normalized["to_id"] not in node_ids:
            unresolved.append({**normalized, "reason": "endpoint_not_found"})
            continue
        edges.append(normalized)

    inventory = {
        "manifest_node_count": len(deduped_nodes),
        "manifest_edge_count": len(edges),
        "unresolved_edge_count": len(unresolved),
        "node_kind_counts": _count_by(deduped_nodes, "kind"),
        "edge_kind_counts": _count_by(edges, "kind"),
    }
    return CompactSessionGraph(
        nodes=tuple(deduped_nodes),
        edges=tuple(edges),
        unresolved_edges=tuple(unresolved),
        inventory=inventory,
    )


def write_compact_session_graph(
    *,
    graph_path: Path,
    nodes: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    edges: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    force: bool = False,
) -> CompactKuzuWriteResult:
    try:
        import kuzu  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(f"kuzu_unavailable:{exc}") from exc

    if graph_path.exists():
        if not force:
            raise RuntimeError(f"graph_path_exists:{graph_path}")
        _remove_path(graph_path)
    graph_path.parent.mkdir(parents=True, exist_ok=True)

    db = kuzu.Database(str(graph_path))
    conn = kuzu.Connection(db)
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS GraphNode(
            id STRING,
            kind STRING,
            packet_id STRING,
            commit_sha STRING,
            label STRING,
            summary STRING,
            properties_json STRING,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS GraphEdge(
            FROM GraphNode TO GraphNode,
            kind STRING,
            properties_json STRING
        )
        """
    )
    for node in nodes:
        conn.execute(
            "CREATE (:GraphNode {"
            f"id: {_q(node.get('id'))}, "
            f"kind: {_q(node.get('kind'))}, "
            f"packet_id: {_q(node.get('packet_id'))}, "
            f"commit_sha: {_q(node.get('commit_sha'))}, "
            f"label: {_q(node.get('label'))}, "
            f"summary: {_q(node.get('summary'))}, "
            f"properties_json: {_q(node.get('properties_json'))}"
            "})"
        )
    for edge in edges:
        conn.execute(
            "MATCH (a:GraphNode), (b:GraphNode) "
            f"WHERE a.id = {_q(edge.get('from_id'))} AND b.id = {_q(edge.get('to_id'))} "
            f"CREATE (a)-[:GraphEdge {{kind: {_q(edge.get('kind'))}, properties_json: {_q(edge.get('properties_json'))}}}]->(b)"
        )
    return CompactKuzuWriteResult(
        ok=True,
        graph_path=str(graph_path),
        node_count=len(nodes),
        edge_count=len(edges),
    )


def _packet_nodes(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        commit_sha = str(commit.get("short_sha") or "")
        out.append(
            _node(
                node_id=packet_id,
                kind="Packet",
                packet_id=packet_id,
                commit_sha=commit_sha,
                label=f"{packet_id} {commit.get('message') or ''}".strip(),
                summary=_join_excerpts(packet.get("problem_refs", []), packet.get("rationale_refs", [])),
                properties=packet,
            )
        )
    return out


def _commit_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("commit_node_id") or f"commit:{item.get('short_sha') or ''}"),
            kind="Commit",
            packet_id=str(item.get("packet_id") or ""),
            commit_sha=str(item.get("short_sha") or ""),
            label=f"{item.get('short_sha') or ''} {item.get('message') or ''}".strip(),
            summary=f"Changed {item.get('changed_files_count') or 0} files",
            properties=item,
        )
        for item in items
    ]


def _evidence_ref_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("evidence_ref_id") or item.get("ref") or ""),
            kind="EvidenceRef",
            packet_id=str(item.get("packet_id") or ""),
            commit_sha=str(item.get("commit_sha") or ""),
            label=str(item.get("ref") or item.get("evidence_ref_id") or ""),
            summary=str(item.get("excerpt") or item.get("command") or ""),
            properties=item,
        )
        for item in items
    ]


def _reasoning_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("reasoning_node_id") or item.get("node_id") or ""),
            kind="ReasoningNode",
            packet_id=str(item.get("source_packet_id") or ""),
            commit_sha=str(item.get("source_commit_sha") or ""),
            label=f"{item.get('node_type') or ''}: {item.get('subject') or ''}".strip(),
            summary=str(item.get("statement") or ""),
            properties=item,
        )
        for item in items
    ]


def _code_hunk_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("hunk_id") or ""),
            kind="CodeHunk",
            packet_id=str(item.get("packet_id") or ""),
            commit_sha=str(item.get("commit_sha") or ""),
            label=f"{item.get('path') or ''}:{item.get('new_start') or ''}",
            summary=f"{item.get('status') or ''} {item.get('path') or ''} {item.get('header') or ''}".strip(),
            properties=item,
        )
        for item in items
    ]


def _code_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("code_node_id") or ""),
            kind="CodeNode",
            packet_id=str(item.get("packet_id") or ""),
            commit_sha=str(item.get("commit_sha") or ""),
            label=f"{item.get('path') or ''}::{item.get('qualified_name') or ''}".strip(":"),
            summary=str(item.get("text_excerpt") or ""),
            properties=item,
        )
        for item in items
    ]


def _symbol_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("symbol_id") or ""),
            kind="Symbol",
            packet_id=str(item.get("latest_packet_id") or item.get("first_packet_id") or ""),
            commit_sha=str(item.get("latest_commit_sha") or item.get("first_commit_sha") or ""),
            label=str(item.get("symbol_key") or item.get("qualified_name") or ""),
            summary=f"{item.get('symbol_kind') or ''} versions={item.get('version_count') or 0}",
            properties=item,
        )
        for item in items
    ]


def _code_version_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _node(
            node_id=str(item.get("version_id") or ""),
            kind="CodeVersion",
            packet_id=str(item.get("packet_id") or ""),
            commit_sha=str(item.get("commit_sha") or ""),
            label=f"{item.get('path') or ''}::{item.get('qualified_name') or ''}@{item.get('commit_sha') or ''}".strip(":"),
            summary=f"{item.get('symbol_kind') or ''} version {item.get('version_index') or ''}".strip(),
            properties=item,
        )
        for item in items
    ]


def _node(
    *,
    node_id: str,
    kind: str,
    packet_id: str,
    commit_sha: str,
    label: str,
    summary: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "packet_id": packet_id,
        "commit_sha": commit_sha,
        "label": _clip(label, 300),
        "summary": _clip(summary, 1000),
        "properties_json": json.dumps(properties, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def _edge(raw_edge: dict[str, Any]) -> dict[str, Any]:
    from_id = str(raw_edge.get("from_id") or _first_matching(raw_edge, "from_", "_id") or "")
    to_id = str(raw_edge.get("to_id") or _first_matching(raw_edge, "to_", "_id") or "")
    kind = str(raw_edge.get("kind") or raw_edge.get("edge_kind") or "")
    return {
        "from_id": from_id,
        "to_id": to_id,
        "kind": kind,
        "properties_json": json.dumps(raw_edge, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def _first_matching(value: dict[str, Any], prefix: str, suffix: str) -> str:
    for key in value:
        if key.startswith(prefix) and key.endswith(suffix):
            return str(value.get(key) or "")
    return ""


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        out.append(node)
    return out


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _join_excerpts(*groups: Any) -> str:
    values: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, dict) and item.get("excerpt"):
                values.append(str(item["excerpt"]))
    return " | ".join(values[:4])


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)].rstrip() + " ... <clipped>"


def _q(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _remove_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()
