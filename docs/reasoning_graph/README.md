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
-> session graph write
-> central_version_merge
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

## Central Merge

The session graph is immutable provenance. It keeps the exact packet, commit,
evidence, hunk, code node, and symbol facts produced by a closed-session V2 job.

`central_version_merge` creates the durable canonical layer beside that session
graph:

```text
session Commit/File/Symbol/CodeRegion
-> KnowledgeVersion
-> VERSION_OF
-> KnowledgeAtom
```

The first production implementation applies only exact deterministic atoms:
`commit`, `file`, `symbol`, and `code_region`. Exact matching uses `repo_id` plus
canonical keys, not local machine paths. If a canonical atom already exists, the
planner emits it as `matched_atoms` and still creates a new `KnowledgeVersion`
for the new session provenance.

Apply writes a `GraphCommit`, updates `GraphView(main, active)`, and writes a
`central_version_merge/merge_result.json` sidecar. `merge_plan.json` remains the
dry-run plan; `merge_result.json`, SQLite `v2_graph_commits`, and SQLite
`v2_graph_views` are the applied-state audit trail.

Decision/problem duplicate, refine, supersede, conflict, and revert relations
are not automatic yet. They remain dry-run/review territory until semantic evals
prove the matching rules are safe.

## Production Reset

Pre-V2 graph and retrieval rows are treated as scrap after the V2 cutover, but
cleanup is never automatic on daemon startup. Operators must run:

```bash
amo-cli v2-reset-production --backup --clean-graph --clean-retrieval
```

New devices with empty production graph/retrieval stores should use normal
install/init instead; `amo-cli v2-init-production` writes the fresh-store marker
without deleting anything.

The command backs up production graph/retrieval/vector stores first, verifies a
backup manifest, then cleans only graph/retrieval/vector/FAISS storage. Raw JSONL
evidence, config, and V2 job tables are preserved.
The production reset marker is written only after both graph and retrieval
cleanup complete, and the runner refuses production Kuzu/retrieval stages if the
marker is missing, version-mismatched, or incomplete.

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
| Knowledge atom | Central canonical identity for an exact commit, file, symbol, or code region |
| Knowledge version | Session-derived version of a central atom with provenance back to session graph |
| Graph commit | Audit object for facts promoted into central memory by one merge apply |
| Graph view | Resolved branch/mode pointer, normally `main/active`, used by retrieval |
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
