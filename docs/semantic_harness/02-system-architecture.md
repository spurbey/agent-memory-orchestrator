# System Architecture

## Target System

```text
Coding agent
  -> explicit amo_harness_query
  -> Semantic Harness local daemon
  -> Repo Knowledge Graph
  -> retrieval/vector projections
  -> strict HarnessCards
```

During migration:

```text
AMO raw evidence / central GraphView / curated graph / answer trace
  -> AMO adapters
  -> Harness repo knowledge graph
```

## Ownership

The harness owns the target agent-facing repo knowledge graph.

AMO remains an evidence and memory source. AMO central GraphView is a migration input, not the final product boundary.

## Logical Versus Physical Graph

Logical graph:

```text
Repo Knowledge Graph
```

Physical projections:

```text
raw JSONL evidence
Kuzu graph store
SQLite job/version/retrieval ledgers
FAISS vector cache
stage artifacts
```

Implementations may store projections separately. The product must expose one identity system and one traversal model.

## Core Components

- Repo indexer: parses files, docs, config, and symbols.
- Graph updater: applies deterministic changes from commits and work windows.
- AMO adapters: import current AMO memory as provenance and migration evidence.
- Query planner: validates intent, resolves anchors, chooses retrieval recipes.
- Retrieval engine: runs exact, graph, lexical, vector, fusion, and rerank stages.
- Traversal engine: walks typed graph paths and filters by active versions.
- Card selector: emits compact high-confidence HarnessCards.
- Eval runner: compares raw tools, current AMO retrieval, and harness cards.

## Data Boundary

Raw evidence is immutable audit data.

Session and work-window data are provenance.

Harness graph nodes and edges are deterministic target identities, version lineage, relation occurrences, and card feedback.

Qwen output is proposal data until deterministic review accepts it.
