from __future__ import annotations

import json

from agent_memory_orchestrator.domain.versioning.central_merge import build_dry_run_merge_plan
from agent_memory_orchestrator.application.pipeline.promotion import build_curated_session_graph


def test_curated_graph_promotes_code_impact_and_keeps_trace_noise_out() -> None:
    packets = [
        {
            "packet_id": "WP0001",
            "commit": {"short_sha": "abc1234", "full_sha": "abc1234", "message": "feat(graph): add graph retrieval"},
        }
    ]
    reasoning = [
        {
            "node_id": "reason:1",
            "node_type": "Decision",
            "subject": "Graph retrieval",
            "statement": "The graph retrieval service explains why graph_service.py changed.",
            "reason": "The implementation adds answer-grade graph retrieval.",
            "source_packet_id": "WP0001",
            "source_commit_sha": "abc1234",
            "evidence_refs": ["E1"],
        }
    ]
    evidence = [{"evidence_ref_id": "E1", "packet_id": "WP0001", "commit_sha": "abc1234", "excerpt": "why graph_service changed"}]
    commits = [{"commit_node_id": "commit:abc1234", "packet_id": "WP0001", "short_sha": "abc1234", "full_sha": "abc1234"}]
    code_nodes = [
        {
            "code_node_id": "code:service",
            "packet_id": "WP0001",
            "commit_sha": "abc1234",
            "path": "src/agent_memory_orchestrator/graph_service.py",
            "node_source": "parsed",
            "symbol_kind": "class",
            "qualified_name": "GraphRagService",
            "line_start": 10,
            "line_end": 40,
            "text_excerpt": "class GraphRagService: ... retrieval ...",
        },
        {
            "code_node_id": "code:import",
            "packet_id": "WP0001",
            "commit_sha": "abc1234",
            "path": "src/agent_memory_orchestrator/graph_service.py",
            "node_source": "parsed",
            "symbol_kind": "import_block",
            "qualified_name": "import_block:1-3",
            "line_start": 1,
            "line_end": 3,
            "text_excerpt": "import json",
        },
    ]

    build = build_curated_session_graph(
        packets=packets,
        reasoning_nodes=reasoning,
        evidence_refs=evidence,
        commit_nodes=commits,
        code_hunks=[],
        code_nodes=code_nodes,
    )
    graph = build.graph.as_dict()
    kinds = {node["kind"] for node in graph["nodes"]}
    labels = {node["label"] for node in graph["nodes"]}

    assert "CodeImpactSummary" in kinds
    assert "FileImpactSummary" in kinds
    assert "FileRef" in kinds
    assert "SymbolRef" in kinds
    assert "CodeRegionRef" in kinds
    assert "src/agent_memory_orchestrator/graph_service.py::GraphRagService" in labels
    assert all("import_block" not in node["label"] for node in graph["nodes"])
    impact = next(node for node in graph["nodes"] if node["kind"] == "CodeImpactSummary")
    assert "explains why graph_service.py changed" in impact["summary"]
    impact_props = json.loads(impact["properties_json"])
    assert impact_props["selected_file_roles"] == {"src/agent_memory_orchestrator/graph_service.py": "primary_implementation"}
    assert impact_props["impact_roles"] == ["primary_implementation"]
    file_impact = next(node for node in graph["nodes"] if node["kind"] == "FileImpactSummary")
    assert "graph_service.py was touched by 1 curated code impact" in file_impact["summary"]
    file_impact_props = json.loads(file_impact["properties_json"])
    assert file_impact_props["primary_impact_role"] == "primary_implementation"
    assert build.audit["policy_counts"]["trace_only"] == 1
    assert build.audit["file_impact_count"] == 1


def test_curated_graph_assigns_ui_roles_and_keeps_tests_as_validation_support() -> None:
    build = build_curated_session_graph(
        packets=[
            {
                "packet_id": "WP0001",
                "commit": {
                    "short_sha": "abc1234",
                    "full_sha": "abc1234",
                    "message": "feat(graph-ui): add spatial graph controls",
                },
            }
        ],
        reasoning_nodes=[
            {
                "node_id": "reason:1",
                "node_type": "Decision",
                "subject": "Spatial graph controls",
                "statement": "The graph UI adds spatial graph controls.",
                "reason": "The implementation changes the web graph canvas.",
                "source_packet_id": "WP0001",
                "source_commit_sha": "abc1234",
                "evidence_refs": [],
            }
        ],
        evidence_refs=[],
        commit_nodes=[{"commit_node_id": "commit:abc1234", "packet_id": "WP0001", "short_sha": "abc1234", "full_sha": "abc1234"}],
        code_hunks=[],
        code_nodes=[
            {
                "code_node_id": "code:css",
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "path": "src/agent_memory_orchestrator/web/amo.css",
                "node_source": "parsed",
                "symbol_kind": "style_rule",
                "qualified_name": ".graph-stage",
                "line_start": 10,
                "line_end": 20,
                "text_excerpt": ".graph-stage { perspective: 800px; }",
            },
            {
                "code_node_id": "code:js",
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "path": "src/agent_memory_orchestrator/web/amo.js",
                "node_source": "parsed",
                "symbol_kind": "function",
                "qualified_name": "onGraphPointerDown",
                "line_start": 20,
                "line_end": 50,
                "text_excerpt": "function onGraphPointerDown() { rotateGraph(); }",
            },
            {
                "code_node_id": "code:test",
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "path": "tests/test_session_cockpit.py",
                "node_source": "parsed",
                "symbol_kind": "function",
                "qualified_name": "test_graph_controls",
                "line_start": 1,
                "line_end": 10,
                "text_excerpt": "def test_graph_controls(): assert True",
            },
        ],
    )

    props_by_label = {node["label"]: json.loads(node["properties_json"]) for node in build.graph.as_dict()["nodes"]}
    assert props_by_label["src/agent_memory_orchestrator/web/amo.css"]["impact_role"] == "ui_style"
    assert props_by_label["src/agent_memory_orchestrator/web/amo.js"]["impact_role"] == "primary_implementation"
    assert props_by_label["tests/test_session_cockpit.py"]["impact_role"] == "validation_test"
    test_symbol = props_by_label["tests/test_session_cockpit.py::test_graph_controls"]
    assert test_symbol["central_atom_candidate"] is False


def test_central_merge_accepts_curated_file_symbol_and_code_region_refs() -> None:
    build = build_curated_session_graph(
        packets=[
            {
                "packet_id": "WP0001",
                "commit": {"short_sha": "abc1234", "full_sha": "abc123456789", "message": "feat(graph): add retrieval"},
            }
        ],
        reasoning_nodes=[
            {
                "node_id": "reason:1",
                "node_type": "Decision",
                "subject": "Graph retrieval",
                "statement": "Graph retrieval uses GraphRagService.",
                "source_packet_id": "WP0001",
                "source_commit_sha": "abc1234",
                "evidence_refs": [],
            }
        ],
        evidence_refs=[],
        commit_nodes=[{"commit_node_id": "commit:abc1234", "packet_id": "WP0001", "short_sha": "abc1234", "full_sha": "abc123456789"}],
        code_hunks=[],
        code_nodes=[
            {
                "code_node_id": "code:service",
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "path": "src/agent_memory_orchestrator/graph_service.py",
                "node_source": "parsed",
                "symbol_kind": "class",
                "qualified_name": "GraphRagService",
                "line_start": 10,
                "line_end": 40,
                "text_excerpt": "class GraphRagService: graph retrieval service",
            }
        ],
    )

    plan = build_dry_run_merge_plan(
        job={
            "job_id": "v2job:test",
            "session_id": "session:test",
            "repo_id": "repo:test",
            "repo_path": "C:/repo",
        },
        compact_graph=build.graph.as_dict(),
        parent_graph_commit_id="",
        existing_atoms_by_canonical_key={},
    )

    atom_kinds = {atom["atom_kind"] for atom in plan.new_atoms}
    assert {"commit", "file", "symbol", "code_region"}.issubset(atom_kinds)
    assert plan.unresolved_identity == []
    assert plan.metrics["exact_atom_created_count"] == 4
    assert json.dumps(plan.as_dict())


def test_central_merge_keeps_low_signal_symbol_refs_as_support_only() -> None:
    build = build_curated_session_graph(
        packets=[
            {
                "packet_id": "WP0001",
                "commit": {"short_sha": "abc1234", "full_sha": "abc123456789", "message": "chore: touch helper"},
            }
        ],
        reasoning_nodes=[
            {
                "node_id": "reason:1",
                "node_type": "Decision",
                "subject": "Helper cleanup",
                "statement": "Helper cleanup happened.",
                "source_packet_id": "WP0001",
                "source_commit_sha": "abc1234",
                "evidence_refs": [],
            }
        ],
        evidence_refs=[],
        commit_nodes=[{"commit_node_id": "commit:abc1234", "packet_id": "WP0001", "short_sha": "abc1234", "full_sha": "abc123456789"}],
        code_hunks=[],
        code_nodes=[
            {
                "code_node_id": "code:helper",
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "path": "src/ui/widget.js",
                "node_source": "parsed",
                "symbol_kind": "function",
                "qualified_name": "paintWidget",
                "line_start": 10,
                "line_end": 40,
                "text_excerpt": "function paintWidget() { return true; }",
            }
        ],
    )

    plan = build_dry_run_merge_plan(
        job={"job_id": "v2job:test", "session_id": "session:test", "repo_id": "repo:test", "repo_path": "C:/repo"},
        compact_graph=build.graph.as_dict(),
        parent_graph_commit_id="",
        existing_atoms_by_canonical_key={},
    )

    atom_kinds = {atom["atom_kind"] for atom in plan.new_atoms}
    assert {"commit", "file", "decision"}.issubset(atom_kinds)
    assert "symbol" not in atom_kinds
    assert "code_region" not in atom_kinds
    assert all(version["status"] == "review" for version in plan.new_versions if version["atom_kind"] == "decision")

