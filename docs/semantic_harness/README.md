# Semantic Harness

Semantic Harness is the next agent-facing product layer for AMO. It is a local-first coding-agent harness that provides repo structure, historical work semantics, version lineage, graph traversal, retrieval, and strict action cards while an agent is actively working.

The harness is not a better retrieval UI. It is a runtime context system for coding agents.

## Locked Decisions

1. The target product has one logical repo knowledge graph.
2. The harness owns that target graph.
3. AMO feeds the harness through adapters during migration.
4. Harness IDs are deterministic and harness-owned.
5. AMO IDs are preserved as provenance, not reused as primary IDs.
6. Vector search is candidate discovery, not graph truth.
7. Qwen proposes semantic frames; deterministic review owns graph mutation.
8. Structural repo graph MVP comes before Qwen and history-heavy implementation.
9. Explicit agent calls come first; automatic sidecar injection is gated by evals.

## Reading Order

1. [Product principles](./00-product-principles.md)
2. [Agent lifecycle problem](./01-agent-lifecycle-problem.md)
3. [System architecture](./02-system-architecture.md)
4. [Graph model](./06-graph-model.md)
5. [Harness query contract](./contracts/amo_harness_query.md)
6. [Bootstrap pipeline](./07-bootstrap-pipeline.md)
7. [Commit update pipeline](./08-commit-update-pipeline.md)
8. [Retrieval and embeddings](./13-retrieval-embeddings-and-projections.md)
9. [Evaluation on real sessions](./16-evaluation-real-sessions.md)
10. [Implementation roadmap](./17-implementation-roadmap.md)

## Fixture-Backed Examples

- [Real AMO rich-history flow](./examples/real-amo-rich-history-flow.md): production fixture `v2job:b387ce0faad2faf4885bd1267106071b`, expected `ready`.
- [Real AMO partial-history flow](./examples/real-amo-partial-history-flow.md): production fixture `v2job:3c901ff20e08a147109af56a301c9207`, expected `partial_historical`.

## Core Lifecycle

```text
first repo bootstrap
-> structural repo graph
-> explicit harness query from agent
-> anchor-first retrieval and graph traversal
-> strict action cards
-> agent edits and validates
-> commit/work-window update
-> deterministic graph update
-> optional Qwen semantic enrichment
-> refreshed versions, relations, cards, and evals
```

## Product Boundary

Current AMO remains the production reasoning-memory system until harness evals prove better agent outcomes. During migration, AMO central GraphView, curated session graph, answer trace, retrieval docs, raw evidence refs, and stage artifacts feed the harness through adapters.

The target state is one harness-owned repo knowledge graph. AMO IDs remain provenance; they do not become primary harness IDs.
