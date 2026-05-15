# Reasoning Graph V2

## Purpose

The Reasoning Graph explains why code changed.

Git already records what changed. AMO V2 adds the reasoning layer around it: problems, causes, decisions, fixes, constraints, evidence, tests, code hunks, symbols, commits, and version relationships.

## V2 Pipeline

```text
raw evidence and transcripts
-> reasoning evidence view
-> commit-backed work packets
-> packet-wise reasoning extraction
-> Git hunks and AST CodeNodes
-> reasoning-to-code linking
-> graph validation
-> isolated Kuzu session graph
-> retrieval docs, embeddings, graph expansion, reranking
-> central graph merge after acceptance
```

## Source of Truth

V2 uses deterministic facts as the spine:

- Git commits define work boundaries.
- Git hunks define changed code regions.
- AST mapping defines CodeNodes and symbols where possible.
- LLM extraction adds reasoning nodes only after validation.
- Kuzu stores graph truth.
- SQLite stores retrieval/index ledgers.
- FAISS is a rebuildable vector cache.

## Read Order

1. [V2 production stage plan](./implementation/11-v2-production-stage-plan.md)
2. [System purpose](./architecture/01-system-purpose.md)
3. [Three-level storage](./architecture/02-three-level-storage.md)
4. [Failure and safety model](./architecture/05-failure-and-safety-model.md)
5. [Node types](./graph_model/node-types.md)
6. [Edge types](./graph_model/edge-types.md)
7. [Provenance and evidence](./graph_model/provenance-and-evidence.md)
8. Algorithm docs under [algorithms](./algorithms/)
9. Module contracts under [modules](./modules/)
10. Examples under [examples](./examples/)

## Stage Boundaries

| Stage | Job | Deterministic or LLM |
| --- | --- | --- |
| 01 raw evidence | Preserve full source events | deterministic |
| 02 evidence view | Filter into answer-grade evidence refs | deterministic |
| 03 work packets | Group by commit and evidence refs | deterministic |
| 04 reasoning extraction | Extract Problem/Cause/Decision/Fix/Constraint/OpenQuestion | local LLM plus validator |
| 05 code graph | Git hunks, AST mapping, CodeNodes, symbol versions | deterministic |
| 06 graph write | Write isolated session graph | deterministic |
| 07 retrieval | BM25/vector/graph expansion/rerank/answer citations | deterministic plus optional local reranker |

## Acceptance Rule

A graph node is not answer-grade unless it can cite packet, commit, evidence, and when applicable code support. Raw tool calls and internal transcript IDs are provenance only; they are not user-facing reasoning nodes.

## Documentation Contract

Detailed docs in this folder should define inputs, outputs, graph effects, validation checks, and failure modes. This README and the V2 production stage plan define the current product path.
