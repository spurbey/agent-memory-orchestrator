# Tree-Sitter AST Expansion

## Depends on
- git-myers-diff-hunks.md
- ../modules/embeddings-runtime.md

## Used by
- code-node-creation.md
- same-file-resolution.md
- ../implementation/03-phase-code-analysis.md

## Related docs
- ../graph_model/node-types.md
- code-node-versioning.md

## Inputs

A `CodeHunk` with current-file line range `[new_start, new_start + new_count - 1]` and the current file content at the target commit.

## Output

One or more AST-bounded code regions for `CodeNode` creation.

## Algorithm

1. Select Tree-sitter grammar by file extension/language.
2. Parse current file content.
3. Convert hunk line range to byte or point range.
4. Find deepest AST node that intersects or contains the hunk start.
5. If hunk spans multiple sibling nodes, create one candidate per intersecting node.
6. Walk upward from each candidate to a meaningful parent.
7. Stop walking when the parent is meaningful and not too broad.

## Meaningful Parent Types

Examples:

- function or method declaration
- class declaration
- object or interface declaration
- assignment or variable declaration when standalone
- import statement
- call expression when the hunk is a dependency/config call
- block for Gradle/Dart/JSON-like config when block size is reasonable

## Stop Rule

Let `hunk_size = max(1, new_count)` and `candidate_size = parent.end_line - parent.start_line + 1`.

Stop at the nearest meaningful parent where:

```text
candidate_size <= max(3 * hunk_size, 12)
```

If the next parent exceeds this limit, keep the current candidate. This prevents a one-line change from expanding into the whole file.

## Multi-Node Hunks

If a hunk spans multiple AST nodes with different meaningful parents, create one `CodeNode` per parent. Each node references the same `CodeHunk` id.

## Fallback

If grammar is missing or parse fails, return a hunk-bounded region with `ast_status=unparsed`. Do not fail the session graph.

## Graph Effects

Feeds `CodeNode` creation with `ast_type`, `ast_status`, line range, and snippet range.

## Tests

- One-line assignment expands to assignment node.
- Function body change expands to function node when size rule permits.
- Tiny hunk inside huge class does not expand to full class.
- Multi-node hunk creates multiple candidates.
- Missing grammar creates `ast_status=unparsed` fallback.