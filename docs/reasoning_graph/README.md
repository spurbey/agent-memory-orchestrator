# Reasoning Graph V2

## Purpose

The Reasoning Graph explains why code changed.

Git already records what changed. AMO V2 adds the reasoning layer around it: problems, causes, decisions, fixes, constraints, evidence, tests, code hunks, symbols, commits, and version relationships.

## V2 Flow

```text
raw evidence and transcripts
-> closed-session V2 job
-> reasoning evidence view
-> commit-backed work packets
-> packet-wise reasoning extraction
-> Git hunks and AST CodeNodes
-> reasoning-to-code linking
-> graph validation
-> V2 Kuzu graph write
-> retrieval docs, embeddings, graph expansion, reranking
```

Production runs this flow through `V2SessionJobRunner`. Drain only detects that
a previous session closed and enqueues a job; it does not run extraction or write
graph nodes. Completed stage artifacts are reused unless their input hash or
stage configuration hash changes.

## Source of Truth

V2 uses deterministic facts as the spine:

- Git commits define work boundaries.
- Git hunks define changed code regions.
- AST mapping defines CodeNodes and symbols where possible.
- LLM extraction adds reasoning nodes only after validation.
- Kuzu stores graph truth.
- SQLite stores retrieval/index ledgers.
- FAISS is a rebuildable vector cache.
- SQLite also stores V2 job state, stage rows, lock leases, retry metadata, and
  the explicit production reset marker.

## Production Reset

Pre-V2 graph and retrieval rows are treated as scrap after the V2 cutover, but
cleanup is never automatic on daemon startup. Operators must run:

```bash
amo-cli v2-reset-production --backup --clean-graph --clean-retrieval
```

The command backs up production graph/retrieval/vector stores first, verifies a
backup manifest, then cleans only graph/retrieval/vector/FAISS storage. Raw JSONL
evidence, config, and V2 job tables are preserved.

Legacy `GraphDelta` generation is available only for `graph-drain-smoke`, which
writes to a disposable smoke graph. Production closed-session processing writes
V2 node kinds such as `Packet`, `Commit`, `EvidenceRef`, `ReasoningNode`,
`CodeHunk`, `CodeNode`, `CodeVersion`, and `Symbol`.

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
