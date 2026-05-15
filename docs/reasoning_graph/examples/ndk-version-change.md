# Example: NDK Version Change

## Depends on
- ../algorithms/git-myers-diff-hunks.md
- ../algorithms/tree-sitter-ast-expansion.md
- ../algorithms/decision-extraction.md

## Used by

## Related docs
- ../algorithms/validated-by-and-test-linking.md
- ../graph_model/central-versioning-rules.md

## Flow

User asks to fix Android build. Agent reads `build.gradle.kts`, sees NDK mismatch, says it will pin NDK because Sentry requires a compatible NDK, edits the file, runs build/test, and commits.

## Timeline

```text
UserMessage -> AgentMessage -> ToolUse(read build.gradle.kts) -> AgentMessage(issue found) -> ToolUse(write build.gradle.kts) -> ToolUse(test) -> SessionEnd
```

## Session Graph

Decision:

```text
DecisionUnit: Pin NDK to 27.0.12077973 because Sentry requires compatible NDK.
confidence: 0.90 after passing test
```

Code:

```text
CodeHunk: @@ -10,1 +10,1 @@
CodeNode: ndk block in build.gradle.kts
prev_content: version 26.1...
content: version 27.0...
```

Edges:

```text
DecisionUnit PRODUCED_CHANGE_IN CodeNode
CodeNode MODIFIES File(build.gradle.kts)
DecisionUnit VALIDATED_BY TestRun
CodeNode LINKED_TO_COMMIT GitCommit
```

## Central Merge

If central graph has broad decision `NDK should be pinned to 27 family`, this new decision `REFINES` it. If central graph has incompatible active decision `NDK should be 26.1`, this new decision `SUPERSEDES` it if evidence supports replacement.
