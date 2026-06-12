# Anchor Resolution

## Purpose

Resolve user terms, file paths, symbols, errors, and recent tool results into graph anchors.

## Inputs

- user goal
- `anchors.files`
- `anchors.symbols`
- `anchors.errors`
- `recent_tool_result`
- repo_id
- active graph view

## Outputs

Resolved anchor nodes with confidence, unresolved terms, and coverage status.

## Algorithm

```text
1. Normalize paths, symbols, stack frames, and error text.
2. Resolve exact file paths.
3. Resolve qualified symbols within file scope.
4. Resolve errors to tests, files, or symbols through lexical and structural hints.
5. Use BM25/vector only for unresolved semantic phrases.
6. Assign coverage status.
```

## Confidence Scoring

Exact path `0.98`, qualified symbol `0.95`, unique symbol name `0.82`, stack frame `0.90`, lexical-only `0.55`, vector-only `0.45` until graph grounded.

## Failure Modes

Multiple same-name symbols produce `partial_coverage`. No anchor returns `unavailable` unless semantic candidates can be grounded.

## Product Usage

Feeds all traversal recipes. Strong anchors allow vector search to be skipped or downweighted.

## Real-Session Eval

Replay an AMO query with a file path and verify exact anchor wins over semantic retrieval.

## Worked Example

Input: `files=["src/auth/session.py"]`, `symbols=["refresh_token"]`.

Intermediate: exact file resolves to `file:repo:src/auth/session.py` confidence `0.98`. Symbol resolves under file to `symbol:repo:src/auth/session.py:AuthSession.refresh_token:method` confidence `0.95`.

Output: coverage `ready`, two resolved anchors, no vector search required.
