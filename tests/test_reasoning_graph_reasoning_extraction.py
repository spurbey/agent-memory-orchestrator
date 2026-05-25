from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.reasoning_graph import review_reasoning_extraction_results
from agent_memory_orchestrator.reasoning_graph import validate_reasoning_node


def _packet() -> dict:
    return {
        "packet_id": "WP0001",
        "commit": {"short_sha": "abc1234"},
        "problem_refs": [{"ref": "E0001"}],
        "rationale_refs": [{"ref": "E0002"}],
        "validation_refs": [{"ref": "E0003"}],
    }


def test_reasoning_extraction_accepts_packet_local_refs() -> None:
    result = review_reasoning_extraction_results(
        packets=[_packet()],
        results=[
            {
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "parsed_output": {
                    "packet_id": "WP0001",
                    "commit_sha": "abc1234",
                    "nodes": [
                        {
                            "node_type": "Decision",
                            "subject": "Local graph",
                            "statement": "Graph truth is stored locally.",
                            "reason": "The packet rationale supports local graph storage.",
                            "confidence": 0.9,
                            "evidence_refs": ["E0002"],
                            "status": "accepted",
                        }
                    ],
                },
            }
        ],
        source_name="unit",
    )

    assert result.summary["stage_acceptance"] == "PASS_WITH_REVIEW_BUCKET"
    assert len(result.accepted_nodes) == 1
    assert result.accepted_nodes[0]["node_id"].startswith("reason:WP0001:abc1234:")
    assert result.accepted_nodes[0]["source_result_file"] == "unit"


def test_reasoning_review_demotes_commit_mismatched_node_to_review() -> None:
    packet = {
        "packet_id": "WP0001",
        "commit": {
            "short_sha": "abc1234",
            "message": "feat(graph-ui): add spatial graph controls",
            "changed_file_sample": [
                "src/agent_memory_orchestrator/web/amo.css",
                "src/agent_memory_orchestrator/web/amo.js",
            ],
        },
        "problem_refs": [{"ref": "E0001"}],
        "rationale_refs": [{"ref": "E0002"}],
        "validation_refs": [{"ref": "E0003"}],
    }
    result = review_reasoning_extraction_results(
        packets=[packet],
        results=[
            {
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "parsed_output": {
                    "packet_id": "WP0001",
                    "commit_sha": "abc1234",
                    "nodes": [
                        {
                            "node_type": "Decision",
                            "subject": "Retrieval default",
                            "statement": "The dashboard exclusively uses the V2 retrieval path.",
                            "reason": "Legacy retrieval fallbacks were removed.",
                            "confidence": 0.9,
                            "evidence_refs": ["E0002"],
                            "status": "accepted",
                        }
                    ],
                },
            }
        ],
        source_name="unit",
    )

    assert result.summary["accepted_node_count"] == 0
    assert result.summary["needs_review_node_count"] == 1
    assert result.summary["diagnostic_kind_counts"]["semantic_alignment_low_overlap"] == 1
    assert result.needs_review_nodes[0]["post_validation"]["semantic_alignment"]["status"] == "low_overlap"


def test_reasoning_extraction_rejects_bad_refs_and_raw_ids() -> None:
    node, diagnostics = validate_reasoning_node(
        {
            "node_type": "Decision",
            "subject": "Bad provenance",
            "statement": "This cites a raw id.",
            "reason": "Bad ref.",
            "confidence": 0.8,
            "evidence_refs": ["transcript:s1:assistant:1"],
            "status": "accepted",
        },
        packet_id="WP0001",
        commit_sha="abc1234",
        index=0,
        allowed_refs={"E0001"},
        validation_refs=set(),
    )

    assert node["post_validation"]["action"] == "reject"
    assert {item["kind"] for item in diagnostics} >= {"evidence_refs_not_in_packet", "raw_internal_id_leak"}


def test_reasoning_extraction_demotes_validation_only_fix() -> None:
    node, diagnostics = validate_reasoning_node(
        {
            "node_type": "Fix",
            "subject": "Tests",
            "statement": "Tests passed.",
            "reason": "Only validation evidence was cited.",
            "confidence": 0.8,
            "evidence_refs": ["E0003"],
            "status": "accepted",
        },
        packet_id="WP0001",
        commit_sha="abc1234",
        index=0,
        allowed_refs={"E0001", "E0003"},
        validation_refs={"E0003"},
    )

    assert node["post_validation"]["action"] == "needs_review"
    assert {item["kind"] for item in diagnostics} >= {"validation_only_reasoning_node", "fix_statement_too_generic"}


def test_real_stage4_final_merge_revalidates_to_expected_counts() -> None:
    root = Path(__file__).resolve().parents[1]
    stage_root = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14"
    packets_path = stage_root / "03b_reasoning_work_packets_strict_validation" / "reasoning_work_packets.json"
    merged_path = stage_root / "04_reasoning_node_extraction_sample" / "stage4_final_merged_reasoning_extraction.json"
    if not (packets_path.exists() and merged_path.exists()):
        return

    packets_data = json.loads(packets_path.read_text(encoding="utf-8"))
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    review = review_reasoning_extraction_results(
        packets=packets_data["packets"],
        results=merged["results"],
        source_name="stage4_final_merged_reasoning_extraction.json",
    )

    assert review.summary["packet_count"] == 59
    assert review.summary["missing_packets"] == []
    assert review.summary["packet_error_ids"] == []
    assert review.summary["accepted_node_count"] == 236
    assert review.summary["needs_review_node_count"] == 68
    assert review.summary["rejected_node_count"] == 0
    assert review.summary["diagnostic_kind_counts"]["semantic_alignment_low_overlap"] == 56
    assert review.summary["accepted_node_type_counts"] == {
        "Problem": 72,
        "Decision": 53,
        "Constraint": 14,
        "Fix": 79,
        "Cause": 18,
    }
