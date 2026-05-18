from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.evidence.raw_store import RawEvidenceStore
from agent_memory_orchestrator.skill_checkpoint import build_skill_checkpoint_prompt
from agent_memory_orchestrator.skill_checkpoint import finalize_skill_checkpoint_result
from agent_memory_orchestrator.skill_checkpoint import infer_latest_session_id
from agent_memory_orchestrator.skill_checkpoint import list_skill_checkpoints
from agent_memory_orchestrator.skill_checkpoint import mark_skill_checkpoint
from agent_memory_orchestrator.skill_checkpoint import write_skill_checkpoint_outputs


def _packet() -> dict:
    return {
        "task": "one_pass_skill_distillation_from_agent_checkpoint",
        "checkpoint": {"checkpoint_id": "skillcp_test"},
        "events": [
            {"ref": "E0001", "type": "user_prompt", "content": "User asked to build a reusable checkpoint skill."},
            {"ref": "E0002", "type": "git_commit", "cmd": "git commit -m feat(skill): add checkpoint flow"},
            {"ref": "E0003", "type": "commit_expansion", "patch": "diff --git a/src/skill.py b/src/skill.py"},
            {"ref": "E0004", "type": "validation", "cmd": "python -m pytest tests/test_skill_checkpoint.py -q"},
        ],
    }


def _qwen_result() -> dict:
    return {
        "parsed_output": {
            "checkpoint_id": "skillcp_test",
            "evidence_cards": [
                {
                    "ref": "E0001",
                    "summary": "User wanted a reusable checkpoint skill.",
                    "semantic_role": "user_goal",
                    "importance": "high",
                    "confidence": 0.9,
                }
            ],
            "work_chains": [
                {
                    "chain_type": "implementation_workflow",
                    "problem_or_goal": "Build a reusable checkpoint skill from a session slice.",
                    "problem_source": "user_stated",
                    "symptom_refs": ["E0001"],
                    "diagnosis_refs": ["E0002"],
                    "action_refs": ["E0002", "E0003"],
                    "validation_refs": ["E0003"],
                    "reusable_pattern": "Select evidence, ask Qwen for sections, validate provenance, render SKILL.md.",
                    "confidence": 0.88,
                }
            ],
            "skill_name": "checkpoint-skill-authoring",
            "description": "Build a reusable skill from a compact checkpoint packet and validated provenance.",
            "skill_sections": {
                "title": "Checkpoint Skill Authoring",
                "overview": "Use a compact checkpoint packet to create a focused reusable agent skill.",
                "when_to_use": ["When a session workflow should become reusable agent behavior."],
                "workflow": [
                    "Select the checkpoint packet with user goal, actions, and validation.",
                    "Use local Qwen to infer the reusable workflow.",
                    "Render SKILL.md only after deterministic validation.",
                ],
                "validation": ["Confirm validation_refs point to validation events."],
                "safety": ["Keep refs and private paths out of rendered SKILL.md."],
            },
            "diagnostics": [],
        }
    }


def test_skill_checkpoint_prompt_uses_sections_not_raw_markdown_output() -> None:
    prompt, prompt_packet, prompt_hash = build_skill_checkpoint_prompt(_packet())

    assert prompt_hash
    assert prompt_packet["prompt_profile"]["schema_version"] == "skill-checkpoint-v1"
    assert "skill_sections" in prompt
    assert "Do not return skill_md or skill_md_lines" in prompt


def test_finalize_repairs_validation_refs_to_actual_validation_events() -> None:
    finalized = finalize_skill_checkpoint_result(result=_qwen_result(), packet=_packet())

    assert finalized["status"] == "accepted"
    assert finalized["error_count"] == 0
    assert finalized["repair_actions"] == [
        {
            "kind": "validation_refs_repaired",
            "chain_index": 0,
            "before": ["E0003"],
            "after": ["E0004"],
            "reason": "validation_refs must point to validation/test events, not commit or expansion events",
        }
    ]
    assert "E0003" not in finalized["skill_md"]
    assert "C:\\Users\\" not in finalized["skill_md"]
    assert finalized["corrected_result"]["parsed_output"]["work_chains"][0]["validation_refs"] == ["E0004"]


def test_finalize_without_repair_rejects_commit_expansion_validation_ref() -> None:
    finalized = finalize_skill_checkpoint_result(
        result=_qwen_result(),
        packet=_packet(),
        auto_repair_validation_refs=False,
    )

    assert finalized["status"] == "needs_review"
    assert finalized["error_count"] == 1
    assert finalized["diagnostics"][0]["kind"] == "validation_ref_not_validation_event"
    assert finalized["diagnostics"][0]["suggested_validation_refs"] == ["E0004"]


def test_write_skill_checkpoint_outputs_writes_rendered_skill_and_provenance(tmp_path: Path) -> None:
    report = write_skill_checkpoint_outputs(result=_qwen_result(), packet=_packet(), out_dir=tmp_path)

    assert report["status"] == "accepted"
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: checkpoint-skill-authoring")
    assert (tmp_path / "skill_provenance.json").exists()
    assert (tmp_path / "stage8_qwen_result_corrected.json").exists()
    assert (tmp_path / "stage8_post_validation_report.json").exists()


def test_mark_skill_checkpoint_infers_latest_session_and_writes_marker(tmp_path: Path, monkeypatch) -> None:
    amo_home = tmp_path / "amo"
    monkeypatch.setenv("AMO_HOME", str(amo_home))
    settings = Settings.load()
    RawEvidenceStore(settings.evidence_dir).append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "codex-session-1"},
        session_id="codex-session-1",
        source_app="codex",
        event_name="user_prompt_submit",
    )

    assert infer_latest_session_id(settings.evidence_dir, source_app="codex") == "codex-session-1"

    result = mark_skill_checkpoint(
        settings=settings,
        agent="codex",
        note="turn this workflow into a skill",
        mode="workflow",
        cwd=tmp_path,
    )

    marker_path = Path(result["marker_path"])
    marker = marker_path.read_text(encoding="utf-8")
    assert result["checkpoint"]["session_id"] == "codex-session-1"
    assert result["checkpoint"]["status"] == "pending_packet"
    assert "turn this workflow into a skill" in marker
    assert result["checkpoint"]["evidence"]["event_name"] == "skill_checkpoint"

    listed = list_skill_checkpoints(settings)
    assert listed["count"] == 1
    assert listed["checkpoints"][0]["checkpoint_id"] == result["checkpoint"]["checkpoint_id"]
