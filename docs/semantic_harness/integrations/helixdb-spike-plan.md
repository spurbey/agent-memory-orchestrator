# HelixDB Spike Plan

## Purpose

Evaluate HelixDB as a backend for graph, text, and vector traversal without
rewriting the current store.

## Policy

HelixDB is spike-only until eval proves value. The AMO planner remains
backend-neutral and owns traversal policy.

## Inputs

- structural graph snapshot
- projection documents
- selected semantic-enriched relation reasons when available
- query mode fixtures

## Spike Scenarios

```text
context_for_anchor
rank_tool_hits
relationship_between_anchors
pre_edit_review structural v1
history_for_anchor when version data exists
```

## Evaluation

Compare against current SQLite/projection backend:

- output quality
- latency
- query complexity
- ability to combine graph/text/vector operations
- debugging clarity

## Constraints

- No arbitrary LLM-authored Helix queries.
- No adoption without measurable benefit.
- Same Query IR must drive both current backend and HelixDB spike.
