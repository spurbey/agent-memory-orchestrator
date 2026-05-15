# Same-File Resolution

## Depends on
- chunking-and-decision-threads.md
- semantic-drift-detection.md
- code-node-versioning.md

## Used by
- ../modules/session-graph-builder.md

## Related docs
- ../examples/same-file-multiple-chunks.md
- ../examples/revert-flow.md

## Problem

A session can touch the same file multiple times. Same file does not always mean same topic. AMO must distinguish continuation, unrelated work, and revert.

## Situation 1: Continuation

Same file, same AST node family, topic similarity `>= 0.75`, no revert signal.

Outcome:

- chunks become same or linked `DecisionThread`
- later code node `REFINES` or `SUPERSEDED_BY` earlier code node
- decision chain remains one reasoning thread

## Situation 2: Unrelated Same-File Change

Same file, topic similarity `< 0.75`, or different AST node with unrelated decision subject.

Outcome:

- separate `DecisionThread`s
- separate `CodeNode`s
- no version edge

## Situation 3: Revert

Same file, same AST node family, topic similarity `>= 0.75`, and message contains revert signals such as `revert`, `undo`, `roll back`, `restore`, or `back out`.

Outcome:

- new decision `REVERTS` old decision
- new code node `REVERTS` old code node
- old active node status becomes `superseded` if revert is applied

## Algorithm

```python
if same_file_touched_before:
    sim = topic_similarity(current_chunk, previous_chunk)
    same_ast = overlaps_same_ast_node(current_code, previous_code)
    revert = has_revert_signal(current_chunk)
    if sim >= 0.75 and same_ast and revert:
        relation = REVERTS
    elif sim >= 0.75 and same_ast:
        relation = REFINES_OR_SUPERSEDES
    elif sim >= 0.75:
        relation = CONTINUES_TOPIC_OF
    else:
        relation = NONE
```

## Tests

- Revisited NDK block after build failure continues topic.
- Compile SDK and Sentry dependency in same Gradle file are separate when topic differs.
- Explicit revert creates revert edges.
