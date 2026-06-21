# Backend-Neutral Query IR

## Purpose

Keep the query planner independent from storage. HelixDB is the authoritative
Semantic Harness graph backend; projection documents are currently rebuilt in
process from the loaded graph.

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

- HelixDB is the production graph backend.
- SQLite is retained only as a legacy migration source and adapter test target.
- Native Helix text/vector execution may replace in-process projection scoring
  without changing the query IR or mode contracts.
- LLMs never write raw HelixDB queries.
- Backends may expose graph, text, and vector operations, but AMO still owns
  planning, scoring, and suppression.
