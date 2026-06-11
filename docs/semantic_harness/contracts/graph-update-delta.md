# Contract: Graph Update Delta

## Purpose

A graph update delta records deterministic and semantic changes from one work window.

## Shape

```json
{
  "delta_id": "string",
  "repo_id": "string",
  "work_window_id": "string",
  "commit_id": "string",
  "created_nodes": [],
  "created_edges": [],
  "updated_edge_weights": [],
  "created_relation_occurrences": [],
  "semantic_review": {
    "accepted": 0,
    "review_only": 0,
    "rejected": 0,
    "quarantined": 0
  },
  "projection_refresh_required": true
}
```

## Rules

- Deltas are idempotent by deterministic IDs.
- Old versions are not deleted.
- Semantic review state is explicit.
- Projection refresh is separate from graph truth.
