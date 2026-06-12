# Contract: Commit Work Window

## Purpose

A commit work window is the structured input for deterministic update and Qwen semantic proposal.

## Shape

```json
{
  "work_window_id": "work:repo:session:hash",
  "repo_id": "repo:example",
  "session_id": "string",
  "commit": {"sha": "string", "message": "string"},
  "user_discussion": [],
  "agent_updates": [],
  "tool_calls": [],
  "tool_observations": [],
  "hunks": [],
  "mapped_entities": [],
  "validation": [],
  "amo_refs": []
}
```

## Rules

- Raw transcript paths are provenance only.
- Machine-local absolute paths must not become identity.
- Missing Qwen does not block deterministic update.
- Work windows must be replayable from raw evidence plus Git state.
