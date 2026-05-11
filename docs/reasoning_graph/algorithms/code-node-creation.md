# Code Node Creation

## Depends on
- git-myers-diff-hunks.md
- tree-sitter-ast-expansion.md
- ../graph_model/node-types.md

## Used by
- code-node-versioning.md
- ../modules/session-graph-builder.md
- ../implementation/03-phase-code-analysis.md

## Related docs
- same-file-resolution.md
- ../modules/git-work-ledger.md

## Inputs

- `CodeHunk`
- AST-expanded region or unparsed hunk region
- previous file content
- current file content
- commit id and patch id when available
- decision thread id

## Output

`CodeNode` with previous and current snippet data.

## Required Fields

```json
{
  "kind": "CodeNode",
  "file_path": "src/app.py",
  "language": "python",
  "ast_type": "function_definition",
  "ast_status": "parsed|unparsed",
  "line_range": [12, 28],
  "prev_content": "old snippet or null",
  "content": "new snippet or null",
  "hunk_id": "code_hunk:...",
  "decision_thread_id": "thread:...",
  "commit_id": "sha",
  "patch_id": "patch id",
  "code_embedding_id": "embedding ref"
}
```

## Algorithm

```python
for region in ast_regions:
    prev = extract_previous_snippet(file, region.old_range)
    curr = extract_current_snippet(file, region.new_range)
    node_id = stable_hash(file_path, commit_id, region.start, region.end, hunk_id)
    create CodeNode(node_id, prev_content=prev, content=curr)
```

## Snippet Rules

Store only meaningful snippet content, not whole files. If snippet exceeds budget, store leading/trailing excerpts and keep Git commit pointer as source of full content.

Deleted code has `content=null` and `prev_content` set.

Added code has `prev_content=null` and `content` set.

Modified code has both.

## Graph Effects

Creates `CodeNode`, links it to `CodeHunk`, `File`, `DecisionThread`, and later `DecisionUnit`.

## Tests

- Added function creates node with null `prev_content`.
- Deleted block creates node with null `content`.
- Modified block stores both snippets.
- Long snippet is bounded and still points to commit.