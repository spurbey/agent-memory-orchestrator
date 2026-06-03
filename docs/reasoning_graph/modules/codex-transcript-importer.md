# Codex Transcript Importer

## Depends on
- raw-evidence-ledger.md
- ../architecture/03-runtime-ownership.md

## Used by
- session-timeline-builder.md

## Related docs
- ../algorithms/chunking-and-decision-threads.md
- ../graph_model/node-types.md

## Purpose

Import visible assistant, user, and tool events from Codex rollout JSONL files referenced by hook `transcript_path`.

## Inputs

`transcript_path`, session id, raw hook evidence rows, optional turn id and evidence time range.

Production imports are turn-window scoped. Hook evidence rows are inspected for
captured `turn_id` values. If any are present, the importer/evidence view reads
only transcript rows between matching Codex `event_msg` records:

```text
task_started(turn_id)
  ... user, assistant, tool call, tool output rows ...
task_complete(turn_id)
```

This prevents resumed Codex rollout files from leaking older sessions into a new
production job. Full transcript import is allowed only when raw evidence has no turn ids
or when an explicit debug path asks for whole-transcript reset behavior.

## Outputs

Normalized transcript events with event type, role, content, tool metadata, timestamp, offset, and source id.

## Owned state

Importer cursors by transcript path and session id. No transcript content is modified.

## Public interfaces planned

- `import_session_transcript(session_id, transcript_path) -> list[NormalizedTimelineEvent]`
- `infer_session_from_transcript(path) -> session_id`

## Kuzu writes

None directly. Timeline builder writes nodes.

## Failure modes

Missing transcript path records diagnostic and continues with hook-only evidence.
Invalid JSON line is skipped with line diagnostic. Duplicate hook/transcript
events are deduped by timestamp, type, tool id, and content hash. If raw turn ids
exist but no transcript task window matches them, the derived evidence view must
report that mismatch instead of silently importing the whole transcript.

## Validation checks

Real Codex transcript import must include at least one assistant message for a
non-empty coding session. For hook-captured production sessions, diagnostics
must include the transcript scope (`raw_turn_window` or `full_transcript`) and
the number of scoped versus skipped transcript lines.
