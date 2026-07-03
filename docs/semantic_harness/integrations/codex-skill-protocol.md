# Codex Skill Protocol

## Purpose

Teach Codex when and how to call Semantic Harness explicitly through the
`amo_harness_query` MCP tool.

This document is the tracked source of truth for the Codex-facing skill. It is
not model-visible by itself. For a live eval, mirror this guidance into a
repo-local Codex skill such as:

```text
.codex/skills/amo-harness/SKILL.md
```

The live skill stays local because `.codex/` is ignored in this repository.

## Tool Contract

Use MCP tool:

```text
amo_harness_query
```

Minimum required parameters:

```json
{
  "repo_id": "repo identifier for the warmed harness graph",
  "intent": "edit_plan|tool_overlay|file_context|why_changed|impact_check|test_plan",
  "user_goal": "current coding task in one sentence"
}
```

Pass anchors whenever they are known:

```json
{
  "files": ["src/example.py"],
  "symbols": ["ExampleService.method"],
  "commits": ["abc123"],
  "errors": ["AssertionError: ..."],
  "recent_tool_result": {
    "tool_name": "rg",
    "summary": "short summary of the last tool output"
  }
}
```

Use strict defaults unless the user asks for history or explanation:

```json
{
  "max_cards": 5,
  "max_tokens": 900,
  "detail": "strict"
}
```

## When To Call

Call `amo_harness_query` when it can change the next investigation step:

- before editing unfamiliar code
- after broad `rg`/grep output with many candidates
- after reading a file whose ownership, dependencies, or history are unclear
- before editing a symbol or file that may affect callers, imports, or tests
- before choosing tests for changed behavior
- when the user asks why a file, function, or relation exists

Do not call it for:

- trivial one-line reads
- formatting-only edits
- already-understood local edits
- command outputs with no file, symbol, error, diff, or test anchor
- repeated questions where the same card IDs were already seen

## Intent Selection

Use this mapping exactly.

| Situation | Intent |
| --- | --- |
| Starting unfamiliar feature or bug work | `edit_plan` |
| After broad `rg`, grep, test output, git diff, or apply result | `tool_overlay` |
| Before editing or deeply reading a known file/symbol | `file_context` |
| Before or after a risky code edit | `impact_check` |
| Before deciding validation commands | `test_plan` |
| User asks why code changed or why a relation exists | `why_changed` |

If two intents seem relevant, use the one closest to the immediate next action.
For example, after a broad search use `tool_overlay`; before editing one of the
returned files use `file_context`.

## Response Handling

Read these response fields first:

```text
status
cards
next_actions
warnings
trace
```

Status behavior:

- `ready`: use high-confidence cards to decide the next file, symbol, or test.
- `partial_structural`: trust structure, but verify history manually.
- `partial_historical`: trust history cautiously, but verify current code.
- `partial_coverage`: use resolved anchors and continue raw exploration for the rest.
- `low_confidence`: do not treat cards as edit instructions.
- `unavailable`: fall back to raw `rg`, file reads, and tests.

Treat `next_actions` with `priority=required` as blocking unless the user
redirects the task.

Use cards as action guidance, not as final truth. Do not claim a fact unless the
card has graph-grounded evidence IDs in `trace` or `evidence`.

## Session State

Pass already seen node, relation, and card IDs when available:

```json
{
  "already_seen_node_ids": [],
  "already_seen_relation_ids": [],
  "already_seen_card_ids": []
}
```

This lets the harness suppress repeated context.

## Eval Logging

During skill-instruction evals, record:

- every `amo_harness_query` call
- intent used
- anchors sent
- cards and next actions returned
- what Codex did in the next one to three tool calls
- whether final touched files overlapped with suggested files

The skill passes only if Codex calls the harness at useful moments and visibly
uses the returned cards to change the investigation path.
