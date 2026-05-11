# Phase 7: Fresh Rebuild

## Depends on
- 01-phase-raw-timeline.md
- 05-phase-central-merge.md
- 06-phase-clustering.md
- ../modules/graph-validation.md

## Used by
- 09-test-and-acceptance-gates.md

## Related docs
- ../architecture/05-failure-and-safety-model.md
- ../modules/kuzu-graph-store.md

## Goal

Back up polluted graph, rebuild a fresh graph from real evidence/transcripts, validate, then swap only if safe.

## Modules touched

Kuzu store, daemon job queue, raw evidence ledger, all graph builders, graph validation.

## Inputs

`AMO_HOME/.evidence`, Codex transcripts, Git repos, current active graph path.

## Outputs

New validated Kuzu graph, backup graph, rebuild report.

## Algorithms used

All prior phase algorithms plus validation gates.

## Kuzu writes

Writes to new graph path until validation passes. Active graph swap is final step.

## CLI/API surface

`graph-rebuild-central --from-real-evidence --from-codex-transcripts --backup-current --apply`.

## Unit tests

Backup/swap behavior and failure rollback.

## Real-data tests

Full rebuild from local AMO evidence and referenced Codex transcripts.

## Pass/fail criteria

Active graph unchanged on failure. Swap only after validators pass.

## Must not do

Do not clean polluted graph in place.