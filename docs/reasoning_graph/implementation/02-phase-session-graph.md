# Phase 2: Session Graph

## Depends on
- 01-phase-raw-timeline.md
- ../modules/extraction-run-manager.md
- ../modules/session-graph-builder.md
- ../algorithms/chunking-and-decision-threads.md

## Used by
- 03-phase-code-analysis.md
- 04-phase-decision-reasoning.md

## Related docs
- ../graph_model/extraction-run-versioning.md
- ../examples/same-file-multiple-chunks.md

## Goal

Create versioned `ExtractionRun` output and decision threads from timeline events.

## Modules touched

Extraction run manager, session graph builder, embeddings runtime.

## Inputs

Timeline graph and session id.

## Outputs

`ExtractionRun`, `DecisionThread`, chunk diagnostics, preliminary session summary.

## Algorithms used

File-switch chunking, explicit phrase chunking, semantic drift, revisit merge.

## Kuzu writes

`ExtractionRun`, `DecisionThread`, `HAS_THREAD`, `CONTINUES_TOPIC_OF`, `CREATED_BY_RUN`.

## CLI/API surface

`graph-build-session --session-id <id> --extraction-run new --apply`.

## Unit tests

Chunk boundaries and thread merging.

## Real-data tests

Real coding session produces multiple decision threads when topics change.

Repeated same-file edits must be tested from real Codex session evidence. The gate requires a real captured timeline where the agent touches the same file more than once after at least one topic boundary. Replayed Git commits or synthetic event fixtures are not acceptable substitutes for this gate because they do not prove timeline chunking, topic revisit, and same-file resolution work on real agent behavior.

Required recorded fields:

- `session_id`
- transcript path or imported Codex rollout path
- AMO evidence ids included in the extraction run
- repeated file path
- event ids for both file-touch segments
- extraction run id

If no such session exists, the implementation is blocked at this gate until one is captured through normal Codex use and processed through the raw timeline importer.

## Pass/fail criteria

No generic-only graph output. Extraction run is complete or partial with diagnostics.

Crash recovery pass criteria:

- daemon crash during chunking leaves the `ExtractionRun` as `partial`
- rerunning the same idempotency key resumes or creates a replacement run
- partial output remains inspectable but cannot be selected until validation passes

## Must not do

Do not central-merge or mutate active graph selection.
