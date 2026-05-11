# Git Myers Diff Hunks

## Depends on
- ../modules/git-work-ledger.md

## Used by
- tree-sitter-ast-expansion.md
- code-node-creation.md
- ../implementation/03-phase-code-analysis.md

## Related docs
- ../graph_model/node-types.md
- code-node-versioning.md

## Purpose

Git already uses Myers-style diff behavior for normal textual diffs. AMO should rely on Git output instead of implementing its own line diff engine.

## Command

For committed changes:

```powershell
git diff --unified=0 <prev_commit> <curr_commit> -- <file>
```

For a single commit:

```powershell
git show --format= --unified=0 <commit> -- <file>
```

`--unified=0` minimizes context so each `@@` block is closer to the atomic changed region.

## Hunk Header Format

```text
@@ -old_start,old_count +new_start,new_count @@ optional section text
```

`old_start` and `old_count` describe the previous file range.

`new_start` and `new_count` describe the current file range. The new range is what Tree-sitter maps to current AST nodes.

If count is omitted, treat it as `1`.

## Output Object

```json
{
  "file_path": "src/app.py",
  "old_start": 10,
  "old_count": 1,
  "new_start": 10,
  "new_count": 3,
  "removed_lines": ["old"],
  "added_lines": ["new"],
  "raw_hunk": "@@ ..."
}
```

## Pseudocode

```python
for file in changed_files:
    diff = git_show_unified_zero(commit, file)
    for block in split_on_hunk_headers(diff):
        header = parse_header(block.header)
        yield CodeHunk(file=file, ranges=header, text=block.text)
```

## Edge Cases

New file: old range may be `0,0`.

Deleted file: new range may be `0,0`; no current AST node exists, but a deleted `CodeNode` can still be recorded with previous content.

Renames should use Git rename metadata when available; otherwise treat as delete plus add.

Binary files do not produce code nodes.

## Graph Effects

Creates `CodeHunk` nodes and feeds `tree-sitter-ast-expansion.md`.

## Tests

- Parses header with explicit counts.
- Parses header with omitted counts.
- Handles added, modified, deleted, and renamed files.
- Extracts `new_start/new_count` correctly for AST expansion.