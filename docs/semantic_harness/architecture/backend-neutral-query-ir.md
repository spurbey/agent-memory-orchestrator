# Backend-Neutral Query IR

## Purpose

Keep the query planner independent from storage. HelixDB is the authoritative
Semantic Harness graph backend; projection documents are currently rebuilt in
process from the bounded evidence graph selected for a query.

## IR Flow

```text
HarnessQueryRequest
-> GraphSlicePlan
-> GraphSeed[] + EdgeExpansion[]
-> EvidenceSubgraph
-> ModeSpecificResult
```

## GraphSlicePlan

Contains:

- query purpose
- unresolved human-facing seeds
- typed edge expansions with direction and depth
- per-expansion neighbor limits
- total node and edge caps

## SeedSet

Contains storage-resolvable starting points:

- files
- symbols
- code regions
- commits
- tests
- relation occurrences
- projection hits

The infrastructure adapter resolves seeds to graph nodes. Vector/BM25 results
may enter as seeds only after they resolve to graph identities.

## TraversalPlan

Contains:

- edge types
- depth limits
- active-version filters
- source-quality filters
- semantic-readiness requirements
- timeout and node/edge caps

The application planner caps unsafe depth and ignores unsupported raw graph
expansion. Helix executes the traversal but does not choose its policy.

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

Normal explicit-mode queries must not call `to_graph()` on the complete store.
Full reconstruction is reserved for migration verification, export/debugging,
legacy compatibility, and graph mutation workflows.

## Backend Rules

- HelixDB is the production graph backend.
- SQLite is retained only as a legacy migration source and adapter test target.
- Native Helix text/vector execution may replace in-process projection scoring
  without changing the query IR or mode contracts.
- LLMs never write raw HelixDB queries.
- Backends may expose graph, text, and vector operations, but AMO still owns
  planning, scoring, and suppression.
