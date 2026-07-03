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
3. [Code structure](./architecture/code-structure.md)
4. [Algorithmic product architecture](./architecture/algorithmic-product-architecture.md)
5. [Semantic fact writer](./architecture/semantic-fact-writer.md)
6. [Mode-based harness query contract](./contracts/mode-based-amo-harness-query.md)
7. [Baseline and phase gates](./evals/baseline-and-phase-gates.md)
8. [Enrichment and embedding eval](./evals/enrichment-and-embedding-eval.md)
9. [Question classification](./algorithms/question-classification.md)
10. [Context for anchor](./algorithms/context-for-anchor.md)
11. [Rank tool hits](./algorithms/rank-tool-hits.md)
12. [Relationship explorer](./algorithms/relationship-explorer.md)
13. [Pre-edit impact reviewer](./algorithms/pre-edit-impact-reviewer.md)
14. [Relation weight scoring](./algorithms/relation-weight-scoring.md)
15. [Qwen/provider enrichment](./integrations/qwen-provider-enrichment.md)
16. [HelixDB spike plan](./integrations/helixdb-spike-plan.md)
17. [MCP to proxy delivery plan](./integrations/mcp-proxy-delivery-plan.md)
18. [Codex proxy spike](./integrations/codex-proxy-spike.md)
19. [Implementation roadmap](./17-implementation-roadmap.md)

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

## Next Algorithmic Direction

The next product work is mode-specific:

```text
rank_tool_hits
relationship_between_anchors
pre_edit_review
history_for_anchor
semantic_diff
```

These modes must use the hierarchy in
[Algorithmic product architecture](./architecture/algorithmic-product-architecture.md).
Do not add new algorithmic behavior to the legacy card query path.

`rank_tool_hits` is the first automatic-delivery candidate. It ranks raw
`rg`/`grep` output with explicit score components plus candidate-local semantic
similarity between the captured user prompt and projection docs attached to the
files/symbols returned by the search. It must preserve raw tool output by
`raw_ref` before any proxy mutation.
