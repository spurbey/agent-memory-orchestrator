from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import (
    AGENT_CHECKPOINT_SCHEMA_VERSION,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import (
    checkpoint_fact_to_semantic_fact_proposal,
)
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import (
    parse_agent_semantic_checkpoint,
)


def test_checkpoint_parser_accepts_valid_v1_json() -> None:
    parsed = parse_agent_semantic_checkpoint(_payload())

    assert parsed.passed
    assert parsed.checkpoint is not None
    assert parsed.checkpoint.checkpoint_id == "checkpoint-1"
    assert parsed.checkpoint.work_windows[0].semantic_facts[0].anchors[0].path == "src/auth.py"


def test_checkpoint_parser_rejects_malformed_json() -> None:
    parsed = parse_agent_semantic_checkpoint("{")

    assert not parsed.passed
    assert parsed.checkpoint is None
    assert parsed.diagnostics[0]["reason"] == "invalid_json"


def test_checkpoint_parser_rejects_missing_work_windows() -> None:
    payload = _payload()
    payload.pop("work_windows")

    parsed = parse_agent_semantic_checkpoint(payload)

    assert not parsed.passed
    assert any(item["reason"] == "missing_work_windows" for item in parsed.diagnostics)


def test_checkpoint_parser_rejects_fact_without_anchors_or_source_refs() -> None:
    payload = _payload()
    fact = payload["work_windows"][0]["semantic_facts"][0]
    fact["anchors"] = []
    fact["source_refs"] = []

    parsed = parse_agent_semantic_checkpoint(payload)

    reasons = {item["reason"] for item in parsed.diagnostics}
    assert not parsed.passed
    assert "missing_anchors" in reasons
    assert "missing_source_refs" in reasons


def test_checkpoint_parser_rejects_unsupported_fact_type() -> None:
    payload = _payload()
    payload["work_windows"][0]["semantic_facts"][0]["fact_type"] = "old_decision_node"

    parsed = parse_agent_semantic_checkpoint(payload)

    assert not parsed.passed
    assert any(item["reason"] == "unsupported_fact_type" for item in parsed.diagnostics)


def test_checkpoint_conversion_produces_semantic_fact_proposal_shape() -> None:
    parsed = parse_agent_semantic_checkpoint(_payload())
    assert parsed.checkpoint is not None
    window = parsed.checkpoint.work_windows[0]
    fact = window.semantic_facts[0]

    proposal = checkpoint_fact_to_semantic_fact_proposal(
        checkpoint=parsed.checkpoint,
        window=window,
        fact=fact,
        anchor_node_ids=("file:repo:test:src/auth.py",),
    )

    assert proposal.fact_type == "implementation_rationale"
    assert proposal.anchor_node_ids == ("file:repo:test:src/auth.py",)
    assert proposal.source_refs[0].ref_id.startswith("checkpoint_ref:")
    assert proposal.source_refs[0].ref_kind == "diff"
    assert proposal.source_kind == "agent_session"
    assert proposal.source_span == "validated_committed"


def _payload() -> dict[str, object]:
    return {
        "schema_version": AGENT_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": "checkpoint-1",
        "parent_session_id": "session-1",
        "repo_root": "C:/repo",
        "base_commit": "abc",
        "head_commit": "def",
        "checkpoint_time": "2026-06-18T12:00:00Z",
        "session_goal": "Preserve auth semantics.",
        "work_windows": [
            {
                "window_id": "window-1",
                "commit_sha": "def",
                "commit_message": "Preserve login None behavior",
                "changed_files": ["src/auth.py"],
                "tests_run": [{"command": "pytest", "status": "passed", "excerpt": "1 passed"}],
                "semantic_facts": [
                    {
                        "fact_type": "implementation_rationale",
                        "text": "Login returns None so older route handlers can treat missing users as anonymous sessions.",
                        "anchors": [
                            {
                                "path": "src/auth.py",
                                "symbol": "login",
                                "line_start": 1,
                                "line_end": 2,
                                "anchor_confidence": 0.9,
                            }
                        ],
                        "source_refs": [
                            {
                                "kind": "diff",
                                "commit_sha": "def",
                                "path": "src/auth.py",
                                "line_start": 1,
                                "line_end": 2,
                                "excerpt": "return None",
                            }
                        ],
                        "derivability": "requires_agent_session_history",
                        "source_kind": "agent_session",
                        "source_span": "validated_committed",
                        "confidence": 0.84,
                    }
                ],
                "rejected_approaches": [],
                "open_questions": [],
            }
        ],
    }
