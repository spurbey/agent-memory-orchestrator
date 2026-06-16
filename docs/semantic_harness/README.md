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

1. [Current vs target architecture](./architecture/current-vs-target.md)
2. [Query mode system](./architecture/query-mode-system.md)
3. [Mode-based harness query contract](./contracts/mode-based-amo-harness-query.md)
4. [Baseline and phase gates](./evals/baseline-and-phase-gates.md)
5. [Question classification](./algorithms/question-classification.md)
6. [Context for anchor](./algorithms/context-for-anchor.md)
7. [Rank tool hits](./algorithms/rank-tool-hits.md)
8. [Qwen/provider enrichment](./integrations/qwen-provider-enrichment.md)
9. [HelixDB spike plan](./integrations/helixdb-spike-plan.md)
10. [Implementation roadmap](./17-implementation-roadmap.md)

The older docs remain useful for graph model, bootstrap, commit updates, and
retrieval details. New product work should start from the mode-based reset docs
above.

## Fixture-Backed Examples

- [Real AMO rich-history flow](./examples/real-amo-rich-history-flow.md): production fixture `v2job:b387ce0faad2faf4885bd1267106071b`, expected `ready`.
- [Real AMO partial-history flow](./examples/real-amo-partial-history-flow.md): production fixture `v2job:3c901ff20e08a147109af56a301c9207`, expected `partial_historical`.

## Core Lifecycle

```text
first repo bootstrap
-> structural repo graph
-> explicit mode-based harness query from agent
-> question classification / tool-hit ranking / pre-edit review
-> mode-specific graph retrieval and traversal
-> compact mode-specific output
-> agent edits and validates
-> commit/work-window update
-> deterministic graph update
-> optional Qwen semantic enrichment
-> refreshed versions, relations, cards, and evals
```

## Product Boundary

Current AMO remains the production reasoning-memory system until harness evals prove better agent outcomes. During migration, AMO central GraphView, curated session graph, answer trace, retrieval docs, raw evidence refs, and stage artifacts feed the harness through adapters.

The target state is one harness-owned repo knowledge graph. AMO IDs remain provenance; they do not become primary harness IDs.

## Reset Policy

Current broad-search and generic card behavior is compatibility/probe behavior.
It should not receive new product features unless the change is a bug fix or a
compatibility repair. New behavior belongs in mode-specific contracts and
modules.
