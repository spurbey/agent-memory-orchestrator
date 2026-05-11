# Codex Transcript Importer

## Depends on
- raw-evidence-ledger.md
- ../architecture/03-runtime-ownership.md

## Used by
- session-timeline-builder.md
- ../implementation/01-phase-raw-timeline.md

## Related docs
- ../algorithms/chunking-and-decision-threads.md
- ../graph_model/node-types.md

## Purpose

Import visible assistant, user, and tool events from Codex rollout JSONL files referenced by hook `transcript_path`.

## Inputs

`transcript_path`, session id, optional turn id and evidence time range.

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

Missing transcript path records diagnostic and continues with hook-only evidence. Invalid JSON line is skipped with line diagnostic. Duplicate hook/transcript events are deduped by timestamp, type, tool id, and content hash.

## Validation checks

Real Codex transcript import must include at least one assistant message for a non-empty coding session.