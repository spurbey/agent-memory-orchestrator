from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol

from ..core.config import Settings
from ..llm.qwen import OllamaQwenClient

SKILL_CHECKPOINT_SCHEMA_VERSION = "skill-checkpoint-v1"
DEFAULT_NUM_PREDICT = 1300
DEFAULT_LOCAL_NUM_CTX = 8192
DEFAULT_PROMPT_PROFILE: dict[str, int] = {
    "authoring_guide_chars": 3600,
    "commit_stat_chars": 900,
    "commit_patch_chars": 1800,
    "stop_summary_chars": 2200,
    "user_prompt_chars": 1800,
    "command_chars": 800,
    "result_chars": 900,
    "generic_chars": 1200,
}

VALIDATION_EVENT_TYPES = {"validation", "test_run", "test_result"}
VALIDATION_RESULT_TYPES = {"tool_result", "command_result"}
ACTION_EVENT_TYPES = {
    "git_commit",
    "commit_expansion",
    "deterministic_commit_expansion",
    "tool_call",
    "tool_result",
    "command_result",
    "code_change",
}
RAW_LEAK_PATTERNS = (
    "tool_use:call_",
    "tool_result:call_",
    "transcript:",
    "\\\\?\\C:",
    "C:\\Users\\",
)

AUTHORING_GUIDE = """Skill authoring rules for Qwen:
- Return one focused reusable capability, not a project summary.
- Write for a future coding agent that must repeat the workflow safely.
- Preserve concrete commands, validation checks, failure modes, and boundaries.
- Do not include evidence refs, raw event ids, transcript ids, or private absolute paths in rendered skill content.
- Generalize private paths as <workspace>, <repo>, <user_home>, <codex_home>, or relative paths.
- Use only these final sections: title, overview, when_to_use, workflow, validation, safety.
- Keep section items compact and practical.
"""


class JsonGenerator(Protocol):
    def generate_json(
        self,
        prompt: str,
        *,
        num_predict: int,
        timeout_seconds: float | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


def build_skill_checkpoint_prompt(
    packet: dict[str, Any],
    *,
    prompt_profile: dict[str, int] | None = None,
) -> tuple[str, dict[str, Any], str]:
    """Build the local-Qwen prompt for one compact skill-checkpoint packet."""

    profile = {**DEFAULT_PROMPT_PROFILE, **(prompt_profile or {})}
    prompt_packet = build_prompt_packet(packet, prompt_profile=profile)
    response_shape = {
        "checkpoint_id": "string",
        "evidence_cards": [
            {
                "ref": "E0001",
                "summary": "short grounded summary",
                "semantic_role": "user_goal|symptom|constraint|diagnosis|workflow_step|fix|validation|context|noise",
                "importance": "high|medium|low",
                "confidence": 0.0,
            }
        ],
        "work_chains": [
            {
                "chain_type": "problem_fix|implementation_workflow|debugging_workflow|validation_workflow",
                "problem_or_goal": "single factual claim",
                "problem_source": "user_stated|inferred_from_events|not_clear",
                "symptom_refs": ["E0001"],
                "diagnosis_refs": ["E0002"],
                "action_refs": ["E0003"],
                "validation_refs": ["E0004"],
                "reusable_pattern": "what future agents should reuse",
                "confidence": 0.0,
            }
        ],
        "skill_name": "lowercase-hyphen-name",
        "description": "specific what-and-when discovery description under 1024 characters",
        "skill_sections": {
            "title": "Skill Title",
            "overview": "One short paragraph.",
            "when_to_use": ["short bullet"],
            "workflow": ["short numbered step"],
            "validation": ["short bullet"],
            "safety": ["short bullet"],
        },
        "diagnostics": [],
    }
    prompt = "\n".join(
        [
            "/no_think",
            "You are Qwen running inside Agent Memory Orchestrator's local skill checkpoint pipeline.",
            "",
            "Goal: build one reusable agent skill from the checkpoint packet.",
            "",
            "Rules:",
            "- This is one pass only.",
            "- AMO selected and mechanically cleaned the checkpoint events.",
            "- Infer meaning from event card content; refs like E0001 are citation handles only.",
            "- Return JSON only. Do not include markdown fences.",
            "- Return skill_sections only; AMO renders final SKILL.md.",
            "- Do not return skill_md or skill_md_lines.",
            "- Every evidence card and work chain ref must cite packet.events[].ref.",
            "- Renderable skill content must not include evidence refs, raw event ids, tool call ids, transcript ids, or private absolute paths.",
            "- Generalize private paths as <workspace>, <repo>, <user_home>, <codex_home>, or relative paths.",
            "- Keep output concise: at most 7 evidence_cards and exactly 1 work_chain.",
            "- skill_sections must include only title, overview, when_to_use, workflow, validation, safety.",
            "",
            "Authoring guide:",
            AUTHORING_GUIDE.strip(),
            "",
            "Required JSON shape:",
            json.dumps(response_shape, ensure_ascii=False, indent=2),
            "",
            "Checkpoint packet:",
            json.dumps(prompt_packet, ensure_ascii=False, separators=(",", ":")),
        ]
    )
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, prompt_packet, prompt_hash


def build_prompt_packet(packet: dict[str, Any], *, prompt_profile: dict[str, int]) -> dict[str, Any]:
    """Mechanically bound packet text while preserving the selected event cards."""

    slim = copy.deepcopy(packet)
    guide = slim.get("authoring_guide")
    if isinstance(guide, dict) and "content" in guide:
        guide["content"] = _clip_text(str(guide.get("content") or ""), prompt_profile["authoring_guide_chars"])

    events: list[dict[str, Any]] = []
    for event in slim.get("events") or []:
        if not isinstance(event, dict):
            continue
        bounded = copy.deepcopy(event)
        event_type = bounded.get("type")
        if event_type in {"deterministic_commit_expansion", "commit_expansion"}:
            bounded["stat"] = _clip_text_container(bounded.get("stat"), prompt_profile["commit_stat_chars"])
            bounded["patch"] = _clip_text_container(bounded.get("patch"), prompt_profile["commit_patch_chars"])
            bounded["patch_excerpt"] = _clip_text_container(
                bounded.get("patch_excerpt"), prompt_profile["commit_patch_chars"]
            )
        elif event_type == "stop_summary":
            bounded["content"] = _clip_text_container(bounded.get("content"), prompt_profile["stop_summary_chars"])
        elif event_type == "user_prompt":
            bounded["content"] = _clip_text_container(bounded.get("content"), prompt_profile["user_prompt_chars"])
        elif event_type in {"git_commit", "validation", "git_status", "git_history"}:
            bounded["cmd"] = _clip_text_container(bounded.get("cmd"), prompt_profile["command_chars"])
            bounded["result"] = _clip_text_container(bounded.get("result"), prompt_profile["result_chars"])
        else:
            bounded["cmd"] = _clip_text_container(bounded.get("cmd"), prompt_profile["generic_chars"])
            bounded["result"] = _clip_text_container(bounded.get("result"), prompt_profile["generic_chars"])
            bounded["content"] = _clip_text_container(bounded.get("content"), prompt_profile["generic_chars"])
        events.append(bounded)

    slim["events"] = events
    slim["prompt_profile"] = {
        "schema_version": SKILL_CHECKPOINT_SCHEMA_VERSION,
        "purpose": "local_qwen_skill_checkpoint",
        "text_limits": prompt_profile,
    }
    return slim


def run_local_skill_checkpoint_extraction(
    *,
    packet: dict[str, Any],
    settings: Settings,
    out_dir: Path,
    num_ctx: int | None = None,
    num_predict: int = DEFAULT_NUM_PREDICT,
    timeout_seconds: float | None = None,
    auto_repair_validation_refs: bool = True,
) -> dict[str, Any]:
    """Run local Ollama/Qwen and write validated skill-checkpoint outputs."""

    prompt, prompt_packet, prompt_hash = build_skill_checkpoint_prompt(packet)
    client = OllamaQwenClient(
        endpoint=settings.qwen_endpoint,
        model=settings.qwen_model,
        timeout_seconds=timeout_seconds or settings.qwen_extract_timeout_seconds,
        num_ctx=max(DEFAULT_LOCAL_NUM_CTX, int(num_ctx or settings.qwen_num_ctx)),
    )
    parsed_output = client.generate_json(
        prompt,
        num_predict=num_predict,
        timeout_seconds=timeout_seconds or settings.qwen_extract_timeout_seconds,
    )
    qwen_result = {
        "schema_version": SKILL_CHECKPOINT_SCHEMA_VERSION,
        "runtime": settings.qwen_runtime,
        "model": settings.qwen_model,
        "call": "skill_checkpoint_one_pass",
        "prompt_hash": prompt_hash,
        "prompt_packet": prompt_packet,
        "parsed_output": parsed_output,
    }
    return write_skill_checkpoint_outputs(
        result=qwen_result,
        packet=packet,
        out_dir=out_dir,
        auto_repair_validation_refs=auto_repair_validation_refs,
    )


def write_skill_checkpoint_outputs(
    *,
    result: dict[str, Any],
    packet: dict[str, Any],
    out_dir: Path,
    auto_repair_validation_refs: bool = True,
) -> dict[str, Any]:
    finalized = finalize_skill_checkpoint_result(
        result=result,
        packet=packet,
        auto_repair_validation_refs=auto_repair_validation_refs,
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    skill_path = out_dir / "SKILL.md"
    provenance_path = out_dir / "skill_provenance.json"
    corrected_result_path = out_dir / "stage8_qwen_result_corrected.json"
    report_path = out_dir / "stage8_post_validation_report.json"

    skill_path.write_text(finalized["skill_md"], encoding="utf-8")
    provenance_path.write_text(json.dumps(finalized["provenance"], indent=2), encoding="utf-8")
    corrected_result_path.write_text(json.dumps(finalized["corrected_result"], indent=2), encoding="utf-8")

    report = {
        "stage": "08_skill_checkpoint_post_validation",
        "schema_version": SKILL_CHECKPOINT_SCHEMA_VERSION,
        "status": finalized["status"],
        "summary": {
            "skill_name": finalized["skill_name"],
            "error_count": finalized["error_count"],
            "warning_count": finalized["warning_count"],
            "skill_path": str(skill_path),
            "provenance_path": str(provenance_path),
            "corrected_result_path": str(corrected_result_path),
        },
        "repair_actions": finalized["repair_actions"],
        "diagnostics": finalized["diagnostics"],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def finalize_skill_checkpoint_result(
    *,
    result: dict[str, Any],
    packet: dict[str, Any],
    auto_repair_validation_refs: bool = True,
) -> dict[str, Any]:
    parsed = result.get("parsed_output")
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(parsed, dict):
        parsed = {}
        diagnostics.append({"level": "error", "kind": "parsed_output_missing"})
    else:
        parsed = copy.deepcopy(parsed)

    events_by_ref = _events_by_ref(packet)
    repair_actions: list[dict[str, Any]] = []
    if auto_repair_validation_refs:
        parsed, repair_actions = repair_validation_refs(parsed, events_by_ref)

    ref_diagnostics, provenance = validate_refs(parsed, events_by_ref)
    diagnostics.extend(ref_diagnostics)

    skill_md = render_skill_md(parsed)
    diagnostics.extend(validate_skill_text(skill_md, parsed))

    error_count = sum(1 for item in diagnostics if item.get("level") == "error")
    warning_count = sum(1 for item in diagnostics if item.get("level") == "warning")

    corrected_result = copy.deepcopy(result)
    corrected_result["parsed_output"] = parsed
    corrected_result["post_validation_repair_actions"] = repair_actions

    return {
        "status": "accepted" if error_count == 0 else "needs_review",
        "skill_name": parsed.get("skill_name"),
        "error_count": error_count,
        "warning_count": warning_count,
        "diagnostics": diagnostics,
        "repair_actions": repair_actions,
        "skill_md": skill_md,
        "provenance": provenance,
        "corrected_result": corrected_result,
    }


def repair_validation_refs(
    parsed: dict[str, Any],
    events_by_ref: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repaired = copy.deepcopy(parsed)
    repair_actions: list[dict[str, Any]] = []
    available = sorted(ref for ref, event in events_by_ref.items() if is_validation_event(event))
    if not available:
        return repaired, repair_actions

    for chain_index, chain in enumerate(repaired.get("work_chains") or []):
        if not isinstance(chain, dict):
            continue
        refs = chain.get("validation_refs", [])
        if not isinstance(refs, list) or not refs:
            continue
        valid = [ref for ref in refs if is_validation_event(events_by_ref.get(ref, {}))]
        invalid = [ref for ref in refs if ref not in valid]
        if invalid:
            replacement = sorted(set(valid + available))
            chain["validation_refs"] = replacement
            repair_actions.append(
                {
                    "kind": "validation_refs_repaired",
                    "chain_index": chain_index,
                    "before": refs,
                    "after": replacement,
                    "reason": "validation_refs must point to validation/test events, not commit or expansion events",
                }
            )
    return repaired, repair_actions


def validate_refs(
    parsed: dict[str, Any],
    events_by_ref: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    used_refs: set[str] = set()
    validation_refs_available = sorted(ref for ref, event in events_by_ref.items() if is_validation_event(event))

    for index, card in enumerate(parsed.get("evidence_cards") or []):
        ref = card.get("ref") if isinstance(card, dict) else None
        used_refs.add(str(ref))
        if ref not in events_by_ref:
            diagnostics.append({"level": "error", "kind": "evidence_card_ref_not_in_packet", "index": index, "ref": ref})

    for chain_index, chain in enumerate(parsed.get("work_chains") or []):
        if not isinstance(chain, dict):
            diagnostics.append({"level": "error", "kind": "work_chain_not_object", "chain_index": chain_index})
            continue
        for field in ("symptom_refs", "diagnosis_refs", "action_refs", "validation_refs"):
            refs = chain.get(field, [])
            if not isinstance(refs, list):
                diagnostics.append(
                    {"level": "error", "kind": "chain_ref_field_not_list", "chain_index": chain_index, "field": field}
                )
                continue
            for ref in refs:
                used_refs.add(str(ref))
                event = events_by_ref.get(ref)
                if event is None:
                    diagnostics.append(
                        {
                            "level": "error",
                            "kind": "chain_ref_not_in_packet",
                            "chain_index": chain_index,
                            "field": field,
                            "ref": ref,
                        }
                    )
                    continue
                event_type = event.get("type")
                if field == "validation_refs" and not is_validation_event(event):
                    diagnostics.append(
                        {
                            "level": "error",
                            "kind": "validation_ref_not_validation_event",
                            "chain_index": chain_index,
                            "ref": ref,
                            "event_type": event_type,
                            "suggested_validation_refs": validation_refs_available,
                        }
                    )
                elif field == "action_refs" and event_type not in ACTION_EVENT_TYPES:
                    diagnostics.append(
                        {
                            "level": "warning",
                            "kind": "action_ref_unusual_event_type",
                            "chain_index": chain_index,
                            "ref": ref,
                            "event_type": event_type,
                        }
                    )

    provenance = {
        "schema_version": SKILL_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": (parsed.get("checkpoint_id") or ""),
        "used_refs": sorted(used_refs),
        "validation_refs_available": validation_refs_available,
        "events": [
            {
                "ref": ref,
                "type": event.get("type"),
                "is_validation": is_validation_event(event),
                "preview": re.sub(r"\s+", " ", event_text(event)).strip()[:280],
            }
            for ref, event in sorted(events_by_ref.items())
            if ref in used_refs
        ],
    }
    return diagnostics, provenance


def render_skill_md(parsed: dict[str, Any]) -> str:
    sections = parsed.get("skill_sections")
    if not isinstance(sections, dict):
        return ""

    skill_name = str(parsed.get("skill_name") or "generated-skill")
    description = str(parsed.get("description") or "Generated skill from AMO checkpoint.").replace("\n", " ").strip()
    title = str(sections.get("title") or skill_name).replace("\n", " ").strip()
    overview = str(sections.get("overview") or "").replace("\n", " ").strip()

    lines = [
        "---",
        f"name: {skill_name}",
        f"description: {description}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    if overview:
        lines.extend([overview, ""])

    section_map = [
        ("When To Use", "when_to_use", "- {item}"),
        ("Workflow", "workflow", "{idx}. {item}"),
        ("Validation", "validation", "- {item}"),
        ("Safety", "safety", "- {item}"),
    ]
    for heading, key, template in section_map:
        lines.extend([f"## {heading}", ""])
        for idx, item in enumerate(_section_items(sections.get(key)), 1):
            lines.append(template.format(idx=idx, item=item.replace("\n", " ").strip()))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def validate_skill_text(skill_md: str, parsed: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if not skill_md.startswith("---"):
        diagnostics.append({"level": "error", "kind": "skill_md_missing_frontmatter"})

    skill_name = parsed.get("skill_name")
    if not isinstance(skill_name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", skill_name or ""):
        diagnostics.append({"level": "error", "kind": "invalid_skill_name", "skill_name": skill_name})

    description = parsed.get("description")
    if not isinstance(description, str) or len(description) > 1024 or len(description.strip()) < 20:
        diagnostics.append({"level": "error", "kind": "invalid_description"})

    sections = parsed.get("skill_sections")
    if not isinstance(sections, dict):
        diagnostics.append({"level": "error", "kind": "invalid_skill_sections"})
    else:
        expected = {"title", "overview", "when_to_use", "workflow", "validation", "safety"}
        missing = sorted(expected - set(sections))
        extra = sorted(set(sections) - expected)
        if missing:
            diagnostics.append({"level": "error", "kind": "missing_skill_sections", "sections": missing})
        if extra:
            diagnostics.append({"level": "warning", "kind": "unexpected_skill_sections", "sections": extra})

    leaked = [pattern for pattern in RAW_LEAK_PATTERNS if pattern in skill_md]
    if leaked:
        diagnostics.append({"level": "error", "kind": "raw_or_private_id_leak", "patterns": leaked})

    evidence_refs = sorted(set(re.findall(r"\bE\d{4}\b", skill_md)))
    if evidence_refs:
        diagnostics.append({"level": "warning", "kind": "evidence_refs_in_rendered_skill", "refs": evidence_refs})

    return diagnostics


def is_validation_event(event: dict[str, Any]) -> bool:
    event_type = event.get("type")
    if event_type in VALIDATION_EVENT_TYPES:
        return True
    if event_type not in VALIDATION_RESULT_TYPES:
        return False
    text = event_text(event).lower()
    return any(marker in text for marker in ("pytest", "ruff check", "tests passed", "test passed", "passed in"))


def event_text(event: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("summary", "content", "cmd", "result", "patch", "patch_excerpt", "stat"):
        value = event.get(key)
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, dict) and isinstance(value.get("text"), str):
            chunks.append(value["text"])
    return "\n".join(chunks)


def _events_by_ref(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        event["ref"]: event
        for event in packet.get("events") or []
        if isinstance(event, dict) and isinstance(event.get("ref"), str)
    }


def _section_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _clip_text(value: str, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n...[TRUNCATED_FOR_QWEN_CONTEXT]...\n{text[-tail:]}"


def _clip_text_container(value: Any, limit: int) -> Any:
    if isinstance(value, dict) and "text" in value:
        out = dict(value)
        original = str(out.get("text") or "")
        out["text"] = _clip_text(original, limit)
        out["trimmed_for_qwen"] = len(original) > limit
        return out
    if isinstance(value, str):
        return _clip_text(value, limit)
    return value
