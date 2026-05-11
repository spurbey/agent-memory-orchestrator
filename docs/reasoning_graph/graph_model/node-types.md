# Node Types

## Depends on
- ../architecture/02-three-level-storage.md
- status-lifecycle.md

## Used by
- edge-types.md
- ../modules/session-graph-builder.md
- ../modules/central-graph-merge-engine.md

## Related docs
- provenance-and-evidence.md
- extraction-run-versioning.md
- ../algorithms/code-node-creation.md

## Required Base Fields

Every graph node must have:

```json
{
  "id": "stable string id",
  "kind": "node kind",
  "label": "short display label",
  "summary": "human-readable summary",
  "scope": "raw|session|central|support",
  "status": "draft|session_final|active|committed|refined|superseded|contested|contested_pending_review|abandoned",
  "session_id": "source session when relevant",
  "project_id": "project namespace",
  "source_app": "codex|claude|slack|system",
  "evidence_ids": ["raw ids"],
  "extraction_run_id": "run id when derived",
  "commit_id": "git commit when code-linked",
  "patch_id": "git patch-id when code-linked",
  "confidence": 0.0,
  "source": "rule|qwen|git|transcript|system",
  "created_at": "ISO timestamp",
  "updated_at": "ISO timestamp",
  "metadata": {}
}
```

## Raw And Timeline Nodes

`RawEvidenceRef` points to append-only evidence file, offset, hash, source app, event name, and timestamp.

`Session` represents one agent session.

`TimelineEvent` is the generic ordered event base for imported transcript or hook events.

`UserMessage`, `AgentMessage`, `ToolUse`, `ToolResult`, and `SessionEnd` are timeline event specializations.

Raw/timeline nodes are not answer-grade and cannot be promoted to central memory.

## Session Summary Nodes

`ExtractionRun` records one extraction attempt over a session timeline.

`DecisionThread` groups chunks that belong to one work topic even if separated in time.

`DecisionUnit` stores one extracted decision with subject, predicate, object, reason, confidence, and source.

`DecisionVersion` records structured version information when one decision refines, supersedes, contradicts, or reverts another.

`CodeHunk` records one parsed Git diff hunk.

`CodeNode` records an AST-bounded or hunk-bounded code region.

`File` represents a code or config file path.

`TestRun` represents a test/lint/build validation event.

`Bug`, `Fix`, `OpenQuestion`, and `SessionSummary` are session-level reasoning artifacts.

## Central Durable Nodes

Central durable nodes are selected session-summary nodes promoted through merge. They retain original session id, extraction run id, evidence ids, commit id, and patch id.

Central answer-grade kinds are `DecisionUnit`, `DecisionVersion`, `CodeNode`, `Bug`, `Fix`, `TestRun`, and selected `SessionSummary` only when it is evidence-backed and not generic.

## Support Nodes

`Repo`, `Branch`, `GitCommit`, `Community`, `App`, and `Project` are support nodes. They help navigation and provenance but are not standalone answers unless explicitly queried.

## Community Nodes

`Community` stores Leiden output and labels. Community membership supports graph navigation and future retrieval scoping. Community nodes do not validate facts and must not override decision statuses.