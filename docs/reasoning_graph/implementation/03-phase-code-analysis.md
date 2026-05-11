# Phase 3: Code Analysis

## Depends on
- ../modules/git-work-ledger.md
- ../algorithms/git-myers-diff-hunks.md
- ../algorithms/tree-sitter-ast-expansion.md
- ../algorithms/code-node-creation.md

## Used by
- 04-phase-decision-reasoning.md
- 05-phase-central-merge.md

## Related docs
- ../algorithms/code-node-versioning.md
- ../graph_model/node-types.md

## Goal

Create hunk and code nodes from real Git diffs and AST expansion.

## Modules touched

Git work ledger, graph code package, embeddings runtime.

## Inputs

Commit id, previous commit, file path, extraction run id.

## Outputs

`CodeHunk`, `CodeNode`, code embeddings, AST fallback metrics.

## Algorithms used

Git unified zero diff parsing, Tree-sitter expansion, code-node creation.

## Kuzu writes

`CodeHunk`, `CodeNode`, `File`, `MODIFIES`, `LINKED_TO_COMMIT`, `CREATED_BY_RUN`.

## CLI/API surface

Included in `graph-build-session` and `graph-validate-session`.

## Unit tests

Hunk parsing, AST expansion, fallback, snippet extraction.

## Real-data tests

Run on a real AMO commit and verify code nodes map to changed files.

For same-file resolution and code-node versioning, the real-data test must use real Codex session evidence with repeated edits to the same file. The accepted source is a captured AMO/Codex timeline and transcript, not a handcrafted fixture and not a synthetic commit sequence.

Minimum same-file gate data:

- real `session_id`
- transcript path or imported Codex rollout path
- evidence ids for the repeated file-touch events
- the repeated file path
- code hunks from the relevant real commit or workspace diff
- extracted decision threads for each file-touch segment

If the current evidence store has no repeated same-file session, capture a small real Codex session that edits one file, switches topic, then returns to edit the same file. Only that real captured session can unblock the same-file resolution gate.

## Pass/fail criteria

Code edits create code nodes. Missing grammar is reported, not silent.

Repeated same-file gate passes only when the real session proves one of:

- same file plus same topic creates `REFINES`, `SUPERSEDED_BY`, or `REVERTS`
- same file plus different topic creates separate code nodes with no version edge
- revert language creates `REVERTS`

No synthetic-only fixture can satisfy this gate.

## Must not do

Do not store whole files as graph node content.
