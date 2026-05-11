# Phase 5: Central Merge

## Depends on
- 04-phase-decision-reasoning.md
- ../modules/central-graph-merge-engine.md
- ../algorithms/entity-resolution.md
- ../algorithms/decision-deduplication.md
- ../algorithms/dependency-propagation.md

## Used by
- 06-phase-clustering.md
- 07-phase-fresh-rebuild.md

## Related docs
- ../graph_model/central-versioning-rules.md
- ../examples/contested-decision-flow.md

## Goal

Promote selected extraction run output into central graph with version edges.

## Modules touched

Central merge engine, Kuzu store, Git work ledger, graph validation.

## Inputs

Selected extraction run, session graph nodes, commit id, patch id, central candidates.

## Outputs

Committed central nodes, version edges, review candidates, contested propagation.

## Algorithms used

Entity resolution, decision dedupe, relationship classification, dependency propagation.

## Kuzu writes

Central answer nodes, `COMMITTED_AS`, version edges, status updates.

## CLI/API surface

`graph-finalize-session --session-id <id> --commit <sha|HEAD> --extraction-run <id> --dry-run|--apply`.

## Unit tests

Promotion gates, dedupe, supersede, conflict, review candidates, no raw promotion.

## Real-data tests

Finalize a real extraction run against a real commit.

## Pass/fail criteria

Only evidence-backed answer nodes promote. Old nodes are preserved.

## Must not do

Do not promote generic summaries or support-only nodes.