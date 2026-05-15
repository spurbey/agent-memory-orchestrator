# Reasoning Graph V2

## Purpose

The Reasoning Graph explains why code changed.

Git already records what changed. AMO V2 adds the reasoning layer around it: problems, causes, decisions, fixes, constraints, evidence, tests, code hunks, symbols, commits, and version relationships.

## V2 Flow

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

## Core Concepts

| Concept | Meaning |
| --- | --- |
| Evidence | Append-only source material captured from hooks, transcripts, tools, and connectors |
| Work packet | Commit-backed unit that groups the problem, rationale, changed files, and validation refs |
| Reasoning node | Validated `Problem`, `Cause`, `Decision`, `Fix`, `Constraint`, or `OpenQuestion` |
| Code node | Hunk or AST-derived code region linked to a packet and commit |
| Symbol version | A versioned symbol view across commits |
| Retrieval document | Searchable text projection of graph nodes for BM25, vector, and rerank retrieval |

## Read Order

1. [System purpose](./architecture/01-system-purpose.md)
2. [Three-level storage](./architecture/02-three-level-storage.md)
3. [Failure and safety model](./architecture/05-failure-and-safety-model.md)
4. [Node types](./graph_model/node-types.md)
5. [Edge types](./graph_model/edge-types.md)
6. [Provenance and evidence](./graph_model/provenance-and-evidence.md)
7. Algorithm docs under [algorithms](./algorithms/)
8. Module contracts under [modules](./modules/)
9. Examples under [examples](./examples/)

## Acceptance Rule

A graph node is not answer-grade unless it can cite packet, commit, evidence, and when applicable code support. Raw tool calls and internal transcript IDs are provenance only; they are not user-facing reasoning nodes.

## Documentation Contract

Detailed docs in this folder should define inputs, outputs, graph effects, validation checks, and failure modes. Keep user-facing docs focused on product behavior.
