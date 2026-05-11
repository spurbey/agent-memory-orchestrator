# Central Graph Merge Engine

## Depends on
- extraction-run-manager.md
- ../graph_model/central-versioning-rules.md
- ../algorithms/entity-resolution.md
- ../algorithms/decision-deduplication.md

## Used by
- ../implementation/05-phase-central-merge.md
- ../implementation/07-phase-fresh-rebuild.md

## Related docs
- qwen-contracts.md
- ../algorithms/dependency-propagation.md
- graph-validation.md

## Purpose

Promote selected session graph nodes into central graph and preserve version history.

## Inputs

Selected extraction run, commit details, diff summary, patch id, session graph nodes, central candidates.

## Outputs

Central nodes, merge/version edges, status updates, review candidates, contested propagation.

## Owned state

Merge plan and merge result records.

## Public interfaces planned

- `plan_merge(session_id, extraction_run_id, commit) -> MergePlan`
- `apply_merge(plan_id) -> MergeResult`

## Kuzu writes

Writes central answer-grade nodes, `COMMITTED_AS`, `DUPLICATE_OF`, `REFINES`, `SUPERSEDES`, `CONFLICTS_WITH`, `REVERTS`, `MODIFIES`, `LINKED_TO_COMMIT`, and status updates.

## Failure modes

Low-confidence Qwen relation becomes review candidate. Missing commit blocks code-linked central promotion unless explicit non-code finalize is requested. Invalid provenance rejects node promotion.

## Validation checks

No raw/support node promoted. Every promoted node has evidence, extraction run, and commit when code-linked.