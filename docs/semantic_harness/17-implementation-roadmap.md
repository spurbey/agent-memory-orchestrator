# Implementation Roadmap

## Principle

Build structural usefulness before semantic richness. If the harness cannot help an agent navigate a repo from structure alone, adding Qwen and history will not fix the product.

## Sequence

### 1. Structural MVP

Build repo bootstrap graph, exact file/symbol lookup, `edit_plan`, `file_context`, and strict cards.

### 2. Retrieval MVP

Add BM25 and vector search over summaries/cards, RRF/fusion, graph traversal, budget enforcement, and novelty filtering.

### 3. AMO Adapters

Import AMO central GraphView, curated graph, answer trace, retrieval docs, and raw evidence refs into harness provenance.

### 4. Commit Update

Add work window processing, Git hunks, hunk-to-symbol confidence, and deterministic version updates.

### 5. Semantic Enrichment

Add Qwen work-causality proposal, deterministic review, accepted ReasoningFrames, and RelationOccurrences.

### 6. Version And Lineage

Add rename, move, split, merge, active version selection, and task-relevant relation occurrence filtering.

### 7. Sidecar

Add automatic tool-result annotation only after false-positive eval passes.

## Release Gates

Each phase must include docs, fixtures, evals, and a rollback path before the next phase starts.
