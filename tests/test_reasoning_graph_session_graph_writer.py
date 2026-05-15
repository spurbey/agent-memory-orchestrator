from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_orchestrator.graph.store import KuzuGraphStore
from agent_memory_orchestrator.reasoning_graph import build_compact_session_graph
from agent_memory_orchestrator.reasoning_graph import write_compact_session_graph


def _minimal_graph():
    return build_compact_session_graph(
        packets=[
            {
                "packet_id": "WP0001",
                "commit": {"short_sha": "abc1234", "message": "feat: add graph"},
                "problem_refs": [{"excerpt": "Need graph"}],
                "rationale_refs": [],
            }
        ],
        reasoning_nodes=[
            {
                "reasoning_node_id": "reason:WP0001:decision:1",
                "node_type": "Decision",
                "subject": "Graph",
                "statement": "Use an isolated graph.",
                "source_packet_id": "WP0001",
                "source_commit_sha": "abc1234",
            }
        ],
        evidence_refs=[{"evidence_ref_id": "E0001", "ref": "E0001", "packet_id": "WP0001", "commit_sha": "abc1234", "excerpt": "Need graph"}],
        commit_nodes=[{"commit_node_id": "commit:abc1234", "packet_id": "WP0001", "short_sha": "abc1234", "message": "feat: add graph"}],
        code_hunks=[],
        code_nodes=[],
        symbols=[],
        code_versions=[],
        raw_edges=[
            {"edge_kind": "REASON_NODE_IN_PACKET", "from_reasoning_node_id": "reason:WP0001:decision:1", "to_packet_id": "WP0001"},
            {"edge_kind": "REASON_NODE_EXPLAINS_COMMIT", "from_reasoning_node_id": "reason:WP0001:decision:1", "to_commit_node_id": "commit:abc1234"},
            {"edge_kind": "REASON_NODE_EVIDENCED_BY", "from_reasoning_node_id": "reason:WP0001:decision:1", "to_evidence_ref_id": "E0001"},
        ],
    )


def test_compact_session_graph_normalizes_nodes_edges_and_inventory() -> None:
    graph = _minimal_graph()

    assert graph.inventory["manifest_node_count"] == 4
    assert graph.inventory["manifest_edge_count"] == 3
    assert graph.inventory["unresolved_edge_count"] == 0
    assert graph.inventory["node_kind_counts"] == {
        "Packet": 1,
        "Commit": 1,
        "EvidenceRef": 1,
        "ReasoningNode": 1,
    }


def test_compact_session_graph_reports_unresolved_edges() -> None:
    graph = build_compact_session_graph(
        packets=[],
        reasoning_nodes=[],
        evidence_refs=[],
        commit_nodes=[],
        code_hunks=[],
        code_nodes=[],
        symbols=[],
        code_versions=[],
        raw_edges=[{"edge_kind": "BROKEN", "from_id": "missing:a", "to_id": "missing:b"}],
    )

    assert graph.inventory["manifest_edge_count"] == 0
    assert graph.inventory["unresolved_edge_count"] == 1
    assert graph.unresolved_edges[0]["reason"] == "endpoint_not_found"


def test_compact_session_graph_writes_kuzu_when_available(tmp_path: Path) -> None:
    pytest.importorskip("kuzu")
    graph = _minimal_graph()
    graph_path = tmp_path / "session_graph.kuzu"

    result = write_compact_session_graph(graph_path=graph_path, nodes=graph.nodes, edges=graph.edges, force=True)

    assert result.ok is True
    store = KuzuGraphStore(graph_path)
    try:
        status = store.merge_status()
        edges = store.list_edges(limit=10)
        hits = store.search_nodes("isolated graph", limit=5)
    finally:
        store.close()
    assert sum(status["counts"].values()) == 4
    assert len(edges) == 3
    assert any(hit["kind"] == "ReasoningNode" for hit in hits)


def test_real_stage5_artifacts_build_expected_compact_graph_manifest() -> None:
    root = Path(__file__).resolve().parents[1]
    stage_root = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14"
    required = {
        "packets": stage_root / "03b_reasoning_work_packets_strict_validation" / "reasoning_work_packets.json",
        "reasoning_nodes": stage_root / "05d_reasoning_code_links" / "reasoning_nodes_with_ids.json",
        "evidence_refs": stage_root / "05d_reasoning_code_links" / "evidence_ref_nodes.json",
        "commit_nodes": stage_root / "05d_reasoning_code_links" / "commit_nodes.json",
        "code_hunks": stage_root / "05a_git_hunks" / "code_hunks.json",
        "code_nodes": stage_root / "05b_ast_code_nodes" / "code_nodes.json",
        "symbols": stage_root / "05c_symbol_versions" / "symbols.json",
        "code_versions": stage_root / "05c_symbol_versions" / "code_node_versions.json",
        "edges": stage_root / "05d_reasoning_code_links" / "stage5d_graph_edges.json",
    }
    if not all(path.exists() for path in required.values()):
        return

    packets_data = json.loads(required["packets"].read_text(encoding="utf-8"))
    graph = build_compact_session_graph(
        packets=packets_data["packets"],
        reasoning_nodes=json.loads(required["reasoning_nodes"].read_text(encoding="utf-8")),
        evidence_refs=json.loads(required["evidence_refs"].read_text(encoding="utf-8")),
        commit_nodes=json.loads(required["commit_nodes"].read_text(encoding="utf-8")),
        code_hunks=json.loads(required["code_hunks"].read_text(encoding="utf-8")),
        code_nodes=json.loads(required["code_nodes"].read_text(encoding="utf-8")),
        symbols=json.loads(required["symbols"].read_text(encoding="utf-8")),
        code_versions=json.loads(required["code_versions"].read_text(encoding="utf-8")),
        raw_edges=json.loads(required["edges"].read_text(encoding="utf-8")),
    )

    assert graph.inventory["manifest_node_count"] == 4411
    assert graph.inventory["manifest_edge_count"] == 10484
    assert graph.inventory["unresolved_edge_count"] == 0
    assert graph.inventory["node_kind_counts"] == {
        "Packet": 59,
        "Commit": 59,
        "EvidenceRef": 498,
        "ReasoningNode": 281,
        "CodeHunk": 479,
        "CodeNode": 1264,
        "Symbol": 719,
        "CodeVersion": 1052,
    }
