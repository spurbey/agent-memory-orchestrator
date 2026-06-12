# Migration Plan

## Current State

AMO owns production memory through raw evidence, curated session graph, central merge, GraphView, retrieval docs, embeddings, FAISS, and answer trace.

## Transition State

Semantic Harness runs in parallel. It imports AMO evidence and central graph projections as provenance while building harness-owned deterministic IDs.

Queries can be compared across:

```text
raw rg/open baseline
current AMO retrieval
semantic harness cards
```

## Target State

Harness owns the agent-facing repo knowledge graph. AMO remains an upstream evidence and memory source.

## Cutover Criteria

Cutover only when:

```text
strict_card_precision >= 0.85
next_file_hit_rate_top3 >= 0.80
test_selection_hit_rate >= 0.75
mislead_rate <= 0.05 for automatic sidecar
real rich and partial fixtures pass
```

## Rollback

Rollback routes agent calls back to current AMO retrieval. Existing AMO evidence and graph stores are not destructively modified.
