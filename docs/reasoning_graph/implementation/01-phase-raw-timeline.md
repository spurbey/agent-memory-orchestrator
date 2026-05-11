# Phase 1: Raw Timeline

## Depends on
- ../modules/raw-evidence-ledger.md
- ../modules/codex-transcript-importer.md
- ../modules/session-timeline-builder.md

## Used by
- 02-phase-session-graph.md

## Related docs
- ../graph_model/node-types.md
- ../graph_model/edge-types.md

## Goal

Build ordered session timelines from real AMO evidence and Codex transcripts.

## Modules touched

Raw evidence ledger, Codex transcript importer, session timeline builder, Kuzu store.

## Inputs

`AMO_HOME/.evidence/*.jsonl`, `transcript_path`, session id.

## Outputs

Timeline nodes and `FOLLOWED_BY` chain.

## Algorithms used

Timestamp and transcript offset ordering, dedupe by type/tool id/content hash, file mention extraction.

## Kuzu writes

`Session`, `RawEvidenceRef`, `TimelineEvent`, `UserMessage`, `AgentMessage`, `ToolUse`, `ToolResult`, `SessionEnd`, `FOLLOWED_BY`, `PART_OF`, `EVIDENCED_BY`.

## CLI/API surface

`graph-import-session --session-id <id> --from-codex-transcript --from-evidence`.

## Unit tests

Ordering, dedupe, transcript import, evidence provenance.

## Real-data tests

Import one real Codex session with assistant messages and tool events.

## Pass/fail criteria

Timeline is ordered, acyclic, provenance-backed, and includes assistant messages.

## Must not do

Do not extract decisions, call Qwen, or create central nodes.