# Example: Code Query Flow

## Depends on
- ../algorithms/code-node-creation.md
- ../algorithms/entity-resolution.md
- ../algorithms/leiden-community-detection.md

## Used by
- future retrieval work

## Related docs
- ../architecture/04-data-flow-end-to-end.md
- ../graph_model/provenance-and-evidence.md

## Scenario

A user pastes:

```kotlin
ndk { version = "27.0.12077973" }
```

## Intended Graph Inspection Path

1. Code embedding finds nearest `CodeNode` for the NDK block.
2. Traverse `PRODUCED_CHANGE_IN` backward to decision.
3. Traverse `CAUSED_BY` to supporting Sentry decision.
4. Traverse `VALIDATED_BY` to test run.
5. Traverse `LINKED_TO_COMMIT` to Git commit.
6. Show community label only as navigation context.

## Expected Answer Shape

```text
This block exists because Decision D pinned NDK to 27.0.12077973 for Sentry compatibility.
It changed CodeNode C in build.gradle.kts lines 9-12, committed as <sha>.
The change was validated by TestRun T.
```

## Rule

The graph answer must cite decision id, code node id, file path, evidence id, and commit id when available.
