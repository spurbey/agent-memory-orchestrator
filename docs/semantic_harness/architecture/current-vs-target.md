# Current Vs Target Architecture

## Purpose

Make the current Semantic Harness state explicit before further implementation.
The current generic card path remains available for compatibility, but new
product work moves to question-driven query modes.

## Current State

The harness currently has useful structural pieces:

- structural repo graph with files, symbols, docs, versions, hunks, and edges
- HelixDB-backed graph storage with in-process projection rebuilding
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

## Evolved Product Direction

The product direction has narrowed from "cards for tool output" to
"mode-specific algorithmic assistance."

Retained:

```text
deterministic harness-owned graph
explicit MCP surface for proving modes
forked-agent semantic checkpoint producer
accepted-only semantic fact attach
projection docs and embeddings as candidate discovery
shadow/proxy exploration as later delivery work
```

Frozen:

```text
generic cards as primary product output
unanchored broad-search cards
tool-overlay attach/suppress tuning as product proof
external provider prompt dumping as live producer
legacy query.py feature growth
```

Deferred:

```text
certified non-derivable product proof
Helix-native vector and advanced traversal execution
proxy replacement or automatic tool-result rewriting
live-query provider calls
semantic-heavy relationship/history scoring before facts exist
```

Next structural algorithm phases:

```text
rank_tool_hits
relationship_between_anchors v1
pre_edit_review v1
relation weight/co-change scoring
```

These phases may proceed before the certified non-derivable eval, but only if
they remain honest about structural-only evidence and do not claim semantic
causality without reviewed facts.

## Non-Negotiable Constraints

- AMO must not duplicate raw `rg`, LSP, import, or caller dumps by default.
- Semantic absence must produce `partial_structural`, `partial_historical`,
  `low_confidence`, or `unavailable`.
- Vector hits are candidates, not truth.
- Qwen/provider output is proposal evidence and must pass deterministic review.
- HelixDB is the authoritative harness graph backend behind a backend-neutral
  query IR; SQLite is a one-time migration source only.
