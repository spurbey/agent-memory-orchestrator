# Backend-Neutral Query IR

## Purpose

Keep the query planner independent from storage. SQLite/projection storage is
the current backend; HelixDB is a spike candidate.

## IR Flow

```text
HarnessQueryRequest
-> QueryPlan
-> SeedSet
-> TraversalPlan
-> EvidenceSubgraph
-> ModeSpecificResult
```

## QueryPlan

Contains:

- mode
- normalized goal and search terms
- resolved anchors
- question classifications
- budget
- required evidence types
- disallowed evidence types
- backend capabilities requested

## SeedSet

Contains graph-grounded starting points:

- files
- symbols
- code regions
- commits
- tests
- relation occurrences
- projection hits

Vector/BM25 results enter as seeds only after they resolve to graph nodes.

## TraversalPlan

Contains:

- edge types
- depth limits
- active-version filters
- source-quality filters
- semantic-readiness requirements
- timeout and node/edge caps

The planner caps unsafe depth and ignores unsupported raw graph expansion.

## EvidenceSubgraph

Contains only selected evidence:

- nodes
- edges
- versions
- paths
- occurrences
- source quality
- confidence and reason codes

This subgraph is the input to output formatting.

## Backend Rules

- SQLite/projection backend remains the default.
- HelixDB is evaluated through the same IR.
- LLMs never write raw HelixDB queries.
- Backends may expose graph, text, and vector operations, but AMO still owns
  planning, scoring, and suppression.
