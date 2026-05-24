from __future__ import annotations

from agent_memory_orchestrator.reasoning_graph.central_merge.decision import build_decision_review_candidates
from agent_memory_orchestrator.reasoning_graph.central_merge.planner import build_dry_run_merge_plan


def test_decision_frames_extract_graph_context_from_compact_edges() -> None:
    result = build_decision_review_candidates(
        compact_graph=_compact_graph(),
        repo_id="repo:test",
        job_id="v2job:test",
        plan_id="v2plan:test",
    )

    assert result["metrics"]["decision_frame_count"] == 2
    first = result["frames"][0]
    assert first["source_node_id"] == "reason:WP0001:abc123:00"
    assert first["linked_packets"] == ["WP0001"]
    assert first["linked_commits"] == ["abc123"]
    assert first["evidence_refs"] == ["E0001"]
    assert first["linked_files"] == ["docs/advisory/server-debug.md"]
    assert first["linked_symbols"] == ["docs/advisory/server-debug.md::ServerDebugRunbook"]


def test_decision_review_candidates_flag_text_overlap_without_auto_merge() -> None:
    result = build_decision_review_candidates(
        compact_graph=_compact_graph(),
        repo_id="repo:test",
        job_id="v2job:test",
        plan_id="v2plan:test",
    )

    assert result["metrics"]["review_candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["proposed_relation"] == "RELATED_REVIEW"
    assert candidate["reason"] == "text_overlap_without_shared_code_context"
    assert candidate["score"]["false_positive_risk"] is True
    assert candidate["score"]["file_overlap"] == 0.0


def test_central_merge_plan_uses_job_repo_id_for_decision_frames() -> None:
    plan = build_dry_run_merge_plan(
        job={"job_id": "v2job:test", "session_id": "session:test", "repo_path": ".", "repo_id": "repo:canonical"},
        compact_graph=_compact_graph(),
        parent_graph_commit_id="",
        existing_atoms_by_canonical_key={},
    )

    assert plan.repo_id == "repo:canonical"
    assert plan.metrics["repo_id_resolution_status"] == "job_repo_id"
    assert {frame["repo_id"] for frame in plan.diagnostics["decision_frames"]} == {"repo:canonical"}
    assert plan.review_candidates[0]["plan_id"] == plan.plan_id


def _compact_graph() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "reason:WP0001:abc123:00",
                "kind": "ReasoningNode",
                "packet_id": "WP0001",
                "commit_sha": "abc123",
                "label": "Decision: Add server debug runbook",
                "summary": "The commit adds server debug runbook and current behavior baseline documentation.",
                "properties_json": (
                    '{"node_type":"Decision","status":"accepted","source_packet_id":"WP0001",'
                    '"source_commit_sha":"abc123","evidence_refs":["E0001"],'
                    '"subject":"Add server debug runbook",'
                    '"statement":"The commit adds server debug runbook and current behavior baseline documentation."}'
                ),
            },
            {
                "id": "reason:WP0002:def456:00",
                "kind": "ReasoningNode",
                "packet_id": "WP0002",
                "commit_sha": "def456",
                "label": "Decision: Add server debug runbook",
                "summary": "The commit introduces server debug runbook and current behavior baseline documentation.",
                "properties_json": (
                    '{"node_type":"Decision","status":"accepted","source_packet_id":"WP0002",'
                    '"source_commit_sha":"def456","evidence_refs":["E0002"],'
                    '"subject":"Add server debug runbook",'
                    '"statement":"The commit introduces server debug runbook and current behavior baseline documentation."}'
                ),
            },
            {
                "id": "code:one",
                "kind": "CodeNode",
                "label": "docs/advisory/server-debug.md::section",
                "properties_json": '{"file_path":"docs/advisory/server-debug.md"}',
            },
            {
                "id": "code:two",
                "kind": "CodeNode",
                "label": "docs/media-storage/server-debug.md::section",
                "properties_json": '{"file_path":"docs/media-storage/server-debug.md"}',
            },
            {
                "id": "symbol:one",
                "kind": "Symbol",
                "label": "ServerDebugRunbook",
                "properties_json": '{"file_path":"docs/advisory/server-debug.md","qualified_name":"ServerDebugRunbook"}',
            },
            {
                "id": "symbol:two",
                "kind": "Symbol",
                "label": "MediaDebugRunbook",
                "properties_json": '{"file_path":"docs/media-storage/server-debug.md","qualified_name":"MediaDebugRunbook"}',
            },
        ],
        "edges": [
            {"from_id": "reason:WP0001:abc123:00", "to_id": "WP0001", "kind": "REASON_NODE_IN_PACKET"},
            {"from_id": "reason:WP0001:abc123:00", "to_id": "commit:abc123", "kind": "REASON_NODE_EXPLAINS_COMMIT"},
            {"from_id": "reason:WP0001:abc123:00", "to_id": "E0001", "kind": "REASON_NODE_EVIDENCED_BY"},
            {"from_id": "reason:WP0001:abc123:00", "to_id": "code:one", "kind": "REASON_NODE_LINKED_TO_CODE_NODE"},
            {"from_id": "reason:WP0001:abc123:00", "to_id": "symbol:one", "kind": "REASON_NODE_LINKED_TO_SYMBOL"},
            {"from_id": "reason:WP0002:def456:00", "to_id": "WP0002", "kind": "REASON_NODE_IN_PACKET"},
            {"from_id": "reason:WP0002:def456:00", "to_id": "commit:def456", "kind": "REASON_NODE_EXPLAINS_COMMIT"},
            {"from_id": "reason:WP0002:def456:00", "to_id": "E0002", "kind": "REASON_NODE_EVIDENCED_BY"},
            {"from_id": "reason:WP0002:def456:00", "to_id": "code:two", "kind": "REASON_NODE_LINKED_TO_CODE_NODE"},
            {"from_id": "reason:WP0002:def456:00", "to_id": "symbol:two", "kind": "REASON_NODE_LINKED_TO_SYMBOL"},
        ],
    }
