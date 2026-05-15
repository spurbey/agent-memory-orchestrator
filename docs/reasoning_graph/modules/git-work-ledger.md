# Git Work Ledger

## Depends on
- ../architecture/01-system-purpose.md
- ../algorithms/git-myers-diff-hunks.md

## Used by
- session-graph-builder.md
- central-graph-merge-engine.md

## Related docs
- ../algorithms/code-node-creation.md
- ../graph_model/central-versioning-rules.md

## Purpose

Read Git facts that anchor AMO reasoning to code truth.

## Inputs

Repository path, commit id or `HEAD`, file path filters.

## Outputs

Commit details, changed files, diff hunks, patch id, branch, repo root.

## Owned state

No owned mutable state. Git remains source of truth.

## Public interfaces planned

- `snapshot(cwd)`
- `commit_details(commit, cwd)`
- `diff_hunks(prev, curr, file)`
- `diff_summary(commit, cwd)`
- `patch_id(commit, cwd)`

## Kuzu writes

None directly. Session and merge modules create Git-related nodes/edges.

## Failure modes

Non-Git directory records `git_available=false`. Missing parent commit handles root commits. Binary diff skipped.

## Validation checks

Patch id stable for same diff. Changed files match Git output. Hunks parse with correct new ranges.
