from __future__ import annotations

import hashlib
from typing import Any

from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from .packet_helpers import _packet_commit_sha


def _hunk_record(packet: dict[str, Any], hunk: dict[str, Any], *, index: int) -> dict[str, Any]:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    patch = str(hunk.get("patch") or "")
    return {
        "hunk_id": f"hunk:{packet.get('packet_id')}:{commit.get('short_sha')}:{index:04d}",
        "original_hunk_id": hunk.get("id"),
        "packet_id": packet.get("packet_id"),
        "commit_sha": commit.get("short_sha"),
        "full_sha": commit.get("full_sha"),
        "commit_message": commit.get("message"),
        "path": hunk.get("file_path"),
        "new_start": hunk.get("new_start"),
        "new_count": hunk.get("new_count"),
        "old_start": hunk.get("old_start"),
        "old_count": hunk.get("old_count"),
        "header": patch.splitlines()[0] if patch else "",
        "hunk_lines": patch.splitlines()[1:],
        "status": "M",
    }


def _code_node_record(packet: dict[str, Any], node: dict[str, Any], *, hunk_id_map: dict[str, str]) -> dict[str, Any]:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    hunk_id = hunk_id_map.get(str(metadata.get("hunk_id") or ""), str(metadata.get("hunk_id") or ""))
    qualified_name = str(metadata.get("symbol_name") or metadata.get("structural_id") or node.get("ast_type") or "")
    content = str(node.get("content") or "")
    digest = hashlib.sha256(f"{packet.get('packet_id')}|{commit.get('short_sha')}|{node.get('id')}".encode("utf-8")).hexdigest()[:20]
    return {
        "code_node_id": f"code:{digest}",
        "packet_id": packet.get("packet_id"),
        "commit_sha": commit.get("short_sha"),
        "full_sha": commit.get("full_sha"),
        "commit_message": commit.get("message"),
        "path": node.get("file_path"),
        "node_source": node.get("ast_status"),
        "symbol_kind": metadata.get("symbol_kind") or node.get("ast_type"),
        "qualified_name": qualified_name,
        "line_start": node.get("line_start"),
        "line_end": node.get("line_end"),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:24],
        "text_excerpt": content[:1800],
        "hunk_ids": [hunk_id] if hunk_id else [],
        "mapped_hunk_count": 1 if hunk_id else 0,
    }


def _symbol_versions(code_nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for node in code_nodes:
        key = f"{node.get('path')}::{node.get('qualified_name')}"
        by_key.setdefault(key, []).append(node)
    symbols: list[dict[str, Any]] = []
    versions: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for key, nodes in sorted(by_key.items()):
        symbol_id = f"symbol:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"
        nodes = sorted(nodes, key=lambda item: (str(item.get("commit_sha") or ""), str(item.get("code_node_id") or "")))
        symbols.append(
            {
                "symbol_id": symbol_id,
                "symbol_key": key,
                "qualified_name": str(nodes[-1].get("qualified_name") or ""),
                "symbol_kind": str(nodes[-1].get("symbol_kind") or ""),
                "first_packet_id": str(nodes[0].get("packet_id") or ""),
                "latest_packet_id": str(nodes[-1].get("packet_id") or ""),
                "first_commit_sha": str(nodes[0].get("commit_sha") or ""),
                "latest_commit_sha": str(nodes[-1].get("commit_sha") or ""),
                "version_count": len(nodes),
            }
        )
        previous_version_id = ""
        for index, node in enumerate(nodes, start=1):
            version_seed = f"{symbol_id}|{node.get('code_node_id')}"
            version_id = f"version:{hashlib.sha256(version_seed.encode('utf-8')).hexdigest()[:20]}"
            versions.append(
                {
                    "version_id": version_id,
                    "symbol_id": symbol_id,
                    "code_node_id": node.get("code_node_id"),
                    "packet_id": node.get("packet_id"),
                    "commit_sha": node.get("commit_sha"),
                    "path": node.get("path"),
                    "qualified_name": node.get("qualified_name"),
                    "symbol_kind": node.get("symbol_kind"),
                    "version_index": index,
                }
            )
            edges.append({"from_id": symbol_id, "to_id": version_id, "kind": "SYMBOL_HAS_VERSION"})
            edges.append({"from_id": version_id, "to_id": symbol_id, "kind": "CODE_VERSION_OF_SYMBOL"})
            edges.append({"from_id": version_id, "to_id": str(node.get("code_node_id") or ""), "kind": "VERSION_CONTAINS_CODE_NODE"})
            edges.append({"from_id": f"commit:{node.get('commit_sha')}", "to_id": version_id, "kind": "COMMIT_HAS_CODE_VERSION"})
            if previous_version_id:
                edges.append({"from_id": previous_version_id, "to_id": version_id, "kind": "VERSION_SUPERSEDED_BY"})
            previous_version_id = version_id
    return symbols, versions, edges


def _relationship_edges(
    packets: list[dict[str, Any]],
    reasoning_nodes: list[dict[str, Any]],
    code_hunks: list[dict[str, Any]],
    code_nodes: list[dict[str, Any]],
    symbol_versions: dict[str, Any],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    packet_by_id = {str(packet.get("packet_id") or ""): packet for packet in packets}
    hunks_by_packet: dict[str, list[dict[str, Any]]] = {}
    nodes_by_packet: dict[str, list[dict[str, Any]]] = {}
    for hunk in code_hunks:
        hunks_by_packet.setdefault(str(hunk.get("packet_id") or ""), []).append(hunk)
    for node in code_nodes:
        nodes_by_packet.setdefault(str(node.get("packet_id") or ""), []).append(node)
    symbols = symbol_versions.get("symbols", []) if isinstance(symbol_versions.get("symbols"), list) else []
    versions = symbol_versions.get("code_versions", []) if isinstance(symbol_versions.get("code_versions"), list) else []
    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        commit_id = f"commit:{_packet_commit_sha(packet)}"
        for hunk in hunks_by_packet.get(packet_id, []):
            edges.append({"from_id": commit_id, "to_id": hunk.get("hunk_id"), "kind": "COMMIT_PRODUCED_HUNK"})
    for node in code_nodes:
        for hunk_id in node.get("hunk_ids", []) if isinstance(node.get("hunk_ids"), list) else []:
            edges.append({"from_id": hunk_id, "to_id": node.get("code_node_id"), "kind": "HUNK_MAPS_TO_CODE_NODE"})
    for node in reasoning_nodes:
        packet_id = str(node.get("source_packet_id") or "")
        node_id = str(node.get("reasoning_node_id") or node.get("node_id") or "")
        packet = packet_by_id.get(packet_id, {})
        edges.append({"from_id": node_id, "to_id": packet_id, "kind": "REASON_NODE_IN_PACKET"})
        edges.append({"from_id": node_id, "to_id": f"commit:{_packet_commit_sha(packet)}", "kind": "REASON_NODE_EXPLAINS_COMMIT"})
        for ref in node.get("evidence_refs", []) if isinstance(node.get("evidence_refs"), list) else []:
            edges.append({"from_id": node_id, "to_id": str(ref), "kind": "REASON_NODE_EVIDENCED_BY"})
        for hunk in hunks_by_packet.get(packet_id, [])[:12]:
            edges.append({"from_id": node_id, "to_id": hunk.get("hunk_id"), "kind": "REASON_NODE_LINKED_TO_HUNK"})
        for code_node in nodes_by_packet.get(packet_id, [])[:12]:
            edges.append({"from_id": node_id, "to_id": code_node.get("code_node_id"), "kind": "REASON_NODE_LINKED_TO_CODE_NODE"})
    for symbol in symbols:
        for node in reasoning_nodes:
            if str(node.get("source_packet_id") or "") == str(symbol.get("latest_packet_id") or symbol.get("first_packet_id") or ""):
                edges.append({"from_id": node.get("node_id"), "to_id": symbol.get("symbol_id"), "kind": "REASON_NODE_LINKED_TO_SYMBOL"})
    for version in versions:
        for node in reasoning_nodes:
            if str(node.get("source_packet_id") or "") == str(version.get("packet_id") or ""):
                edges.append({"from_id": node.get("node_id"), "to_id": version.get("version_id"), "kind": "REASON_NODE_LINKED_TO_CODE_VERSION"})
    edges.extend(symbol_versions.get("edges", []) if isinstance(symbol_versions.get("edges"), list) else [])
    return [edge for edge in edges if edge.get("from_id") and edge.get("to_id") and edge.get("kind")]


def _versioned_items(value: Any, job: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                **item,
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "session_id": job.get("session_id"),
                "job_id": job.get("job_id"),
                "repo_id": job.get("repo_id") or "",
                "repo_path": job.get("repo_path") or "",
            }
        )
    return out


def _evidence_ref_nodes(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for packet in packets:
        for group, kind in (("problem_refs", "problem"), ("rationale_refs", "rationale"), ("validation_refs", "validation")):
            for ref in packet.get(group, []) if isinstance(packet.get(group), list) else []:
                if not isinstance(ref, dict) or not ref.get("ref"):
                    continue
                refs[str(ref["ref"])] = {
                    "evidence_ref_id": ref["ref"],
                    "ref": ref["ref"],
                    "packet_id": packet.get("packet_id"),
                    "commit_sha": _packet_commit_sha(packet),
                    "evidence_kind": kind,
                    "excerpt": ref.get("excerpt") or ref.get("output_preview") or ref.get("command") or "",
                }
    return list(refs.values())


def _commit_nodes(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = []
    for packet in packets:
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        nodes.append(
            {
                "commit_node_id": f"commit:{commit.get('short_sha')}",
                "packet_id": packet.get("packet_id"),
                "short_sha": commit.get("short_sha"),
                "full_sha": commit.get("full_sha"),
                "message": commit.get("message"),
                "changed_files_count": commit.get("changed_files_count"),
            }
        )
    return nodes
