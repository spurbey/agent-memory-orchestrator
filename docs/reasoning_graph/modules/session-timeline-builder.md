# Session Timeline Builder

## Depends on
- raw-evidence-ledger.md
- codex-transcript-importer.md
- ../graph_model/node-types.md

## Used by
- session-graph-builder.md
- ../implementation/01-phase-raw-timeline.md

## Related docs
- ../algorithms/chunking-and-decision-threads.md
- ../graph_model/edge-types.md

## Purpose

Create an ordered timeline graph from hook evidence and transcript events.

## Inputs

Raw evidence records and normalized transcript events for one session.

## Outputs

Timeline nodes and `FOLLOWED_BY` chain.

## Owned state

Timeline build diagnostics and dedupe index for event ids/content hashes.

## Public interfaces planned

- `build_timeline(session_id, evidence_records, transcript_events) -> TimelineBuildResult`

## Kuzu writes

Creates `Session`, `TimelineEvent`, `UserMessage`, `AgentMessage`, `ToolUse`, `ToolResult`, `SessionEnd`, `FOLLOWED_BY`, `PART_OF`, `MENTIONS_FILE`, and `EVIDENCED_BY`.

## Failure modes

Missing assistant messages lowers validation score. Ambiguous ordering falls back to evidence order and records diagnostic.

## Validation checks

Timeline must be acyclic, ordered, and include provenance for every node.