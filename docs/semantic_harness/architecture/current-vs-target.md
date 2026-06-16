# Current Vs Target Architecture

## Purpose

Make the current Semantic Harness state explicit before further implementation.
The current generic card path remains available for compatibility, but new
product work moves to question-driven query modes.

## Current State

The harness currently has useful structural pieces:

- structural repo graph with files, symbols, docs, versions, hunks, and edges
- SQLite-backed graph/projection storage
- explicit MCP tool named `amo_harness_query`
- shadow tool-context planner for captured tool results
- projection documents with lexical and deterministic vector retrieval
- deterministic commit-update deltas and co-change occurrence seeds

The current public response is still card-centric. Recent broad-search and
tool-overlay commits are probe behavior, not the target product shape.

## Probe Path Policy

These commits are retained as compatibility and learning artifacts:

- `94f00cf` grounded tool overlay cards
- `0112314` broad search focus cards
- `d3ad577` unanchored retrieval to source-file cards

Policy:

- keep them unless they break existing behavior
- fix bugs only
- do not extend generic card behavior as the product architecture
- migrate useful ideas into mode-specific modules after evals prove value

## Target State

The target harness is a coding-agent decision system:

```text
agent request with mode, goal, question, anchors, and budget
-> AMO validates and plans
-> graph/text/vector retrieval and traversal run under strict policy
-> AMO returns mode-specific compact output
-> agent chooses the next tool call or edit action
```

The target behavior is not "return cards for everything." Each mode has its own
shape:

- `context_for_anchor` answers a specific semantic question
- `rank_tool_hits` ranks search output
- `pre_edit_review` reviews a planned edit
- `relationship_between_anchors` explains multi-anchor linkage
- `history_for_anchor` explains version/work history
- `semantic_diff` reviews an actual patch

## Non-Negotiable Constraints

- AMO must not duplicate raw `rg`, LSP, import, or caller dumps by default.
- Semantic absence must produce `partial_structural`, `partial_historical`,
  `low_confidence`, or `unavailable`.
- Vector hits are candidates, not truth.
- Qwen/provider output is proposal evidence and must pass deterministic review.
- HelixDB is a spike candidate behind a backend-neutral query IR.
