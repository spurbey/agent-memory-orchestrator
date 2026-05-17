# Skill Checkpoint Pipeline

## Purpose

Skill checkpoints turn a bounded agent/user work slice into one reusable agent skill.

The goal is not to summarize a chat. The goal is to preserve a repeated workflow so a future agent can reuse it without rediscovering the same process or making the same mistake.

Final user-facing output is a clean `SKILL.md`. Provenance, evidence refs, checkpoint ids, and validation details stay in AMO metadata files.

## Current Production Scope

Production now supports the validated Stage 8 core:

1. Read one compact checkpoint packet.
2. Send the packet to local Ollama/Qwen, normally `qwen3.5:9b`.
3. Require Qwen to return structured `skill_sections`, not raw Markdown.
4. Render `SKILL.md` deterministically inside AMO.
5. Validate evidence refs and rendered skill safety.
6. Repair safe provenance-only mistakes, such as non-validation refs in `validation_refs`.
7. Write the skill, provenance, corrected result, and validation report.

Checkpoint selection and slash-command UX are intentionally not finalized yet. Those will decide how users mark a checkpoint during a live Codex/Claude session. The production core here starts after a compact packet already exists.

## Why This Shape Exists

Stage 8 reached this shape after several failures:

| Attempt | Result | Decision |
| --- | --- | --- |
| Full raw JSONL-style checkpoint packet | Too large and noisy | Do not send full raw session dumps to Qwen. |
| Colab Qwen with large context/output budget | GPU memory pressure | Keep packets compact and mechanically bounded. |
| Ask Qwen for raw `skill_md` string | JSON escaped multiline output was fragile | AMO should render Markdown, not Qwen. |
| Ask Qwen for `skill_md_lines` | Model inserted invalid newlines inside list items | Still too formatting-sensitive. |
| Ask Qwen for `skill_sections` | Parsed cleanly and rendered reliably | Keep this as the production contract. |
| Let Qwen choose all refs freely | It used commit-expansion refs as validation refs | Add AMO post-validation and safe provenance repair. |

The important boundary is:

- Qwen infers semantic meaning and writes compact section content.
- AMO owns packet selection, clipping, validation, rendering, provenance, and installation.

## Why Evidence Refs Exist

Refs like `E0001` are not meant for the final skill reader. They are internal handles back to the compact packet event cards.

AMO needs them for:

- grounding Qwen claims in the actual checkpoint packet,
- debugging why Qwen inferred a workflow,
- validating that `validation_refs` point to test/validation evidence,
- storing provenance separately from clean skill content,
- rebuilding or comparing the skill later with a different model/prompt.

The rendered `SKILL.md` must not contain refs, raw transcript ids, tool-call ids, or private absolute paths.

## Input Packet

The current accepted input is a compact JSON packet with event cards:

```json
{
  "task": "one_pass_skill_distillation_from_agent_checkpoint",
  "checkpoint": {"checkpoint_id": "skillcp_example"},
  "events": [
    {"ref": "E0001", "type": "user_prompt", "content": "..."},
    {"ref": "E0002", "type": "git_commit", "cmd": "..."},
    {"ref": "E0003", "type": "commit_expansion", "patch": "..."},
    {"ref": "E0004", "type": "validation", "cmd": "python -m pytest ..."}
  ]
}
```

Current event types used by the validator:

- `user_prompt`
- `git_commit`
- `commit_expansion`
- `deterministic_commit_expansion`
- `tool_call`
- `tool_result`
- `command_result`
- `validation`
- `test_run`
- `test_result`
- `stop_summary`

## Qwen Output Contract

Qwen must return JSON with:

```json
{
  "checkpoint_id": "skillcp_example",
  "evidence_cards": [],
  "work_chains": [],
  "skill_name": "lowercase-hyphen-name",
  "description": "specific what-and-when description",
  "skill_sections": {
    "title": "Skill Title",
    "overview": "One short paragraph.",
    "when_to_use": ["short bullet"],
    "workflow": ["short step"],
    "validation": ["short bullet"],
    "safety": ["short bullet"]
  },
  "diagnostics": []
}
```

Qwen must not return `skill_md` or `skill_md_lines`. AMO renders the final Markdown.

## Commands

Run local Qwen extraction:

```powershell
amo-cli skill-checkpoint extract `
  --packet .tmp/reasoning-graph-v2-reset-2026-05-14/08_skill_checkpoint_simulation/skill_checkpoint_qwen_packet_workflow.compact.json `
  --out-dir .tmp/skill-checkpoint-run `
  --num-ctx 8192 `
  --num-predict 1300
```

Finalize a returned Qwen result without calling Qwen:

```powershell
amo-cli skill-checkpoint finalize `
  --result C:\Users\sumit\Downloads\stage8_skill_checkpoint_qwen35_9b_4bit_result.json `
  --packet .tmp/reasoning-graph-v2-reset-2026-05-14/08_skill_checkpoint_simulation/skill_checkpoint_qwen_packet_workflow.compact.json `
  --out-dir .tmp/skill-checkpoint-run
```

Outputs:

- `SKILL.md`
- `skill_provenance.json`
- `stage8_qwen_result_corrected.json`
- `stage8_post_validation_report.json`

## Validation Rules

Hard failures:

- missing parsed output,
- bad `skill_name`,
- missing or invalid `skill_sections`,
- evidence refs not present in the packet,
- `validation_refs` pointing to non-validation events,
- raw transcript/tool ids or private absolute paths in rendered `SKILL.md`.

Safe automatic repair:

- If Qwen places commit refs in `validation_refs`, AMO can replace them with actual validation refs from the same packet.
- This repair changes provenance only. It does not rewrite the skill prose.

## Still To Build

These are intentionally left for the next phase:

- live checkpoint marking UX, such as a slash command or explicit AMO checkpoint command,
- checkpoint-window selection from hook evidence,
- installing the rendered skill into Codex/Claude discovery paths,
- skill quality scoring across multiple checkpoints,
- graph links from generated skills back to checkpoint/session/version nodes.
