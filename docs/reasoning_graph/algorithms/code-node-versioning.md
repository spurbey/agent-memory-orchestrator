# Code Node Versioning

## Depends on
- code-node-creation.md
- same-file-resolution.md
- ../graph_model/central-versioning-rules.md

## Used by
- ../modules/central-graph-merge-engine.md
- ../implementation/05-phase-central-merge.md

## Related docs
- decision-deduplication.md
- ../examples/revert-flow.md

## Inputs

New `CodeNode`, existing session or central `CodeNode` candidates, decision thread topic embedding, file path, AST type, line range, and revert signals.

## Candidate Query

Fetch existing code nodes where:

- same normalized file path, and
- same AST type or same `ast_status=unparsed`, and
- overlapping line range or same stable symbol path when available.

## Relationship Rules

If same file and same AST node family:

- topic similarity `>= 0.75` and no revert signal -> `REFINES` or `SUPERSEDED_BY` depending content replacement.
- topic similarity `>= 0.75` and revert signal -> `REVERTS`.
- topic similarity `< 0.75` -> unrelated, no version edge.

If same file but different AST node:

- same topic -> `CONTINUES_TOPIC_OF` between threads, separate code nodes.
- different topic -> no version relation.

## Prev Content Rule

When a new node versions an old node, set:

```text
new_code_node.prev_content = old_code_node.content
```

If Git previous snippet disagrees with old graph content, record diagnostic and prefer Git as code truth.

## Status Rule

Old code node becomes `superseded` only when the relationship is replacement or revert. It remains `active/refined` when the new node merely adds detail.

## Pseudocode

```python
candidates = find_same_file_overlapping_nodes(new_node)
for old in candidates:
    sim = topic_similarity(new_thread, old.thread)
    if sim < 0.75:
        continue
    if has_revert_signal(new_thread):
        edge = REVERTS
        old.status = superseded
    elif replaces_content(old, new_node):
        edge = SUPERSEDED_BY
        old.status = superseded
    else:
        edge = REFINES
```

## Tests

- Same AST node, same topic creates version edge.
- Same file, different topic creates no version edge.
- Revert signal creates `REVERTS` and supersedes old node.
- Old node is preserved, not deleted.