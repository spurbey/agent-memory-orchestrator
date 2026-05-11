# Dependency Propagation

## Depends on
- ../graph_model/edge-types.md
- ../graph_model/status-lifecycle.md

## Used by
- ../modules/central-graph-merge-engine.md
- ../implementation/05-phase-central-merge.md

## Related docs
- decision-deduplication.md
- ../examples/contested-decision-flow.md

## Purpose

When a central decision is superseded or invalidated, downstream decisions depending on it may become unsafe. AMO must surface those nodes instead of silently leaving them active.

## Inputs

A changed decision node and relation such as `SUPERSEDES`, `REVERTS`, or `INVALIDATES`.

## Algorithm

Run BFS over outgoing dependency edges:

```python
queue = [changed_node]
seen = set()
while queue:
    node = queue.pop(0)
    for dependent in incoming_neighbors(node, edge_kind="DEPENDS_ON"):
        if dependent.id in seen:
            continue
        seen.add(dependent.id)
        mark(dependent, "contested_pending_review")
        create_edge(changed_node, dependent, "INVALIDATES")
        queue.append(dependent)
```

## Stop Conditions

Stop at already superseded or abandoned nodes unless they have active downstream dependents.

Stop after configured max depth, default `5`, and create diagnostic if truncated.

## Output

Updated downstream statuses and `INVALIDATES` edges. Contested surfacing payload lists affected nodes, source decision, and evidence.

## Tests

- One-hop dependent becomes contested pending review.
- Multi-hop dependents are reached.
- Cycles do not loop forever.
- Abandoned branches are skipped unless active descendants exist.