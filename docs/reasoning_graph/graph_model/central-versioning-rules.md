# Central Versioning Rules

## Depends on
- node-types.md
- edge-types.md
- status-lifecycle.md
- extraction-run-versioning.md

## Used by
- ../modules/central-graph-merge-engine.md
- ../algorithms/code-node-versioning.md
- ../algorithms/decision-deduplication.md

## Related docs
- ../algorithms/entity-resolution.md
- ../algorithms/dependency-propagation.md
- ../examples/contested-decision-flow.md

## Purpose

Central graph versioning preserves how knowledge evolves. New knowledge is added as new nodes and version edges. Old knowledge remains available with updated status.

## No Delete Rule

Central graph merge must not delete historical answer-grade nodes. It may add edges and update statuses.

## Decision Versioning

If new decision means the same as old decision, create `DUPLICATE_OF` and add evidence. Do not create a new active duplicate unless the new evidence must stand independently.

If new decision is more specific and compatible, create `REFINES`. Old node becomes `refined`.

If new decision replaces old decision, create `SUPERSEDES` or `SUPERSEDED_BY` according to edge direction used by implementation. Old node becomes `superseded`.

If new decision conflicts with old decision and neither is proven dominant, create `CONFLICTS_WITH`. Both nodes become `contested` or review candidates.

## Code Node Versioning

Code nodes version by file plus AST identity or overlapping line range. New code nodes keep `prev_content` and link to prior code node when they represent an evolution of the same code region.

## Commit Anchoring

Promoted nodes must link to Git with `COMMITTED_AS` or `LINKED_TO_COMMIT` when a commit exists. Commit id and patch id must be stored on promoted code/decision nodes when code-linked.

## Embedding Rule

Embeddings are append-only. Do not mutate old node embeddings when a node is superseded. New nodes get new embeddings.