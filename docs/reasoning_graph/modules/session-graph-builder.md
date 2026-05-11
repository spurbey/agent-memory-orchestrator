# Session Graph Builder

## Depends on
- session-timeline-builder.md
- extraction-run-manager.md
- ../algorithms/chunking-and-decision-threads.md
- ../algorithms/decision-extraction.md

## Used by
- central-graph-merge-engine.md
- ../implementation/02-phase-session-graph.md

## Related docs
- ../algorithms/code-node-creation.md
- ../algorithms/relationship-extraction.md
- graph-validation.md

## Purpose

Convert a session timeline into a versioned session summary graph.

## Inputs

Timeline graph, extraction run id, Git diff/code data, embeddings, Qwen contracts.

## Outputs

Decision threads, decisions, bugs, fixes, code nodes, tests, relationships, and session summary.

## Owned state

Session graph diagnostics for one extraction run.

## Public interfaces planned

- `build_session_graph(session_id, extraction_run_id) -> SessionGraphBuildResult`

## Kuzu writes

Writes `DecisionThread`, `DecisionUnit`, `CodeHunk`, `CodeNode`, `TestRun`, `Bug`, `Fix`, `OpenQuestion`, `SessionSummary`, and reasoning edges.

## Failure modes

Missing code grammar falls back to unparsed code nodes. Qwen failures become diagnostics. Insufficient graph shape marks run `partial`.

## Validation checks

A coding session must produce decision threads and code nodes, not only generic summaries.