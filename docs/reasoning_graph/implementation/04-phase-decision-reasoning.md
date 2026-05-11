# Phase 4: Decision Reasoning

## Depends on
- 02-phase-session-graph.md
- 03-phase-code-analysis.md
- ../algorithms/decision-extraction.md
- ../algorithms/relationship-extraction.md
- ../algorithms/validated-by-and-test-linking.md

## Used by
- 05-phase-central-merge.md

## Related docs
- ../modules/qwen-contracts.md
- ../graph_model/provenance-and-evidence.md

## Goal

Extract decisions, bugs, fixes, tests, and typed relationships inside the selected extraction run.

## Modules touched

Decision package, Qwen contracts, session graph builder.

## Inputs

Decision threads, code nodes, tests, timeline events.

## Outputs

`DecisionUnit`, `Bug`, `Fix`, `OpenQuestion`, `TestRun`, relationship edges.

## Algorithms used

Rule extraction, Qwen fallback, relationship extraction, `VALIDATED_BY` linking.

## Kuzu writes

Decision/test/fix nodes and reasoning/validation edges.

## CLI/API surface

`graph-validate-session --session-id <id>` exposes extraction quality.

## Unit tests

Decision patterns, Qwen gates, relationship edges, validation confidence bump.

## Real-data tests

Real coding session yields decision-to-code and validation links when tests exist.

## Pass/fail criteria

Every answer-grade node has evidence and extraction run id.

## Must not do

Do not let Qwen write directly to central graph.