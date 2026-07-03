---
name: amo-semantic-checkpoint
description: Emit structured AMO semantic checkpoint JSON from a forked same-context coding session after a checkpoint boundary.
---

# AMO Semantic Checkpoint

Use this skill only when explicitly asked to create an AMO semantic checkpoint
for completed work in this repo.

You are an AMO semantic checkpoint agent. You are running in a fork of the
coding session. Use only information available from session start up to this
checkpoint moment.

Treat the checkpoint boundary as current `HEAD` plus explicitly included
uncommitted changes. If work was committed before this fork, analyze commits
from `session_start_commit` or `base_commit` to current `HEAD`. Do not infer
future intent.

Your job is to produce structured repo-semantic fact proposals for work that
actually happened in this checkpoint window.

## Multi-Pass Workflow

1. Identify the checkpoint range.
   Use `session_start_commit` or `base_commit` through current `HEAD`.

2. Build a work-window inventory.
   Include commits, changed files, tests or validation, and the user goal.

3. Inspect each commit or window.
   Read changed files and relevant hunks. Identify semantic decisions that
   survived into code. Include rejected approaches only when explicitly visible.

4. Emit compact facts.
   Use at most 3-5 semantic facts per work window and at most 1-2 facts per file
   unless risk-critical. Skip mechanical changes.

5. Self-review.
   Remove generic facts, unsupported facts, facts without anchors/source refs,
   and temporary hypotheses unless they are explicitly recorded as a rejected
   approach.

6. Write final JSON to:
   `.tmp/amo-semantic-checkpoints/<checkpoint_id>/semantic_checkpoint.json`

## Hard Bans

Do not output:

- raw transcript dumps
- future intent
- temporary hypotheses as truth
- generic facts like "updated code", "fixed bug", or "changed function"
- invented files, symbols, tests, commits, or source refs
- direct graph node IDs unless AMO supplied an allowed-node catalog

## Output Schema

The root object must use:

```json
{
  "schema_version": "amo-agent-semantic-checkpoint-v1",
  "checkpoint_id": "string",
  "parent_session_id": "string",
  "repo_root": "string",
  "base_commit": "string",
  "head_commit": "string",
  "checkpoint_time": "ISO-8601 string",
  "session_goal": "string",
  "work_windows": []
}
```

Each work window:

```json
{
  "window_id": "string",
  "commit_sha": "string",
  "commit_message": "string",
  "changed_files": ["repo-relative path"],
  "tests_run": [
    {
      "command": "string",
      "status": "passed|failed|unknown",
      "excerpt": "short text"
    }
  ],
  "semantic_facts": [],
  "rejected_approaches": [],
  "open_questions": []
}
```

Each semantic fact:

```json
{
  "fact_type": "semantic_role|invariant_or_contract|implementation_rationale|risk_or_impact|relationship_reason|validation_expectation|historical_change",
  "text": "specific, non-generic fact",
  "anchors": [
    {
      "path": "repo-relative path",
      "symbol": "optional function/class/method name",
      "code_region_hint": "optional block/branch/section hint",
      "line_start": 0,
      "line_end": 0,
      "anchor_confidence": 0.0,
      "ambiguity": "optional short explanation"
    }
  ],
  "source_refs": [
    {
      "kind": "diff|commit_message|test_output|tool_call|user_instruction|agent_final_reason|provider_eval",
      "commit_sha": "string",
      "path": "optional path",
      "line_start": 0,
      "line_end": 0,
      "command": "optional command",
      "excerpt": "short support excerpt"
    }
  ],
  "derivability": "derivable_from_current_code|requires_git_history|requires_agent_session_history|requires_human_intent|mixed|unknown",
  "source_kind": "agent_session",
  "source_span": "validated_committed|final_summary",
  "confidence": 0.0
}
```

Use paths, symbols, and lines. Do not output AMO graph node IDs.
