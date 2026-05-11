# Example: Same File Multiple Chunks

## Depends on
- ../algorithms/same-file-resolution.md
- ../algorithms/chunking-and-decision-threads.md

## Used by
- ../implementation/02-phase-session-graph.md

## Related docs
- ../algorithms/code-node-versioning.md
- ../graph_model/edge-types.md

## Scenario

The same file `build.gradle.kts` is touched three times in one session.

## Continuation

Chunk 1 pins NDK. Chunk 4 revisits NDK after build failure and adjusts exact version. Same file, same AST block, topic similarity `0.88`.

Outcome:

```text
chunk_4 CONTINUES_TOPIC_OF chunk_1
code_node_v1 SUPERSEDED_BY code_node_v2
```

## Unrelated Same File

Chunk 1 fixes NDK. Chunk 2 adds Sentry dependency. Same file but different AST node and topic similarity `0.31`.

Outcome:

```text
separate DecisionThread nodes
separate CodeNode nodes
no version edge
```

## Revert

Chunk 1 pins NDK. Chunk 5 says reverting NDK pin because it breaks Mapbox. Same AST node and revert signal.

Outcome:

```text
new_decision REVERTS old_decision
new_code_node REVERTS old_code_node
old nodes preserved with superseded status
```