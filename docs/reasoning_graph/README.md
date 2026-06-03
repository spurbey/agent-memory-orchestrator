# Reasoning Graph

## Purpose

The Reasoning Graph explains why code changed.

Git already records what changed. AMO adds the reasoning layer around it: problems, causes, decisions, fixes, constraints, evidence, tests, code hunks, symbols, commits, and version relationships.

Before commit-backed stages run, the production pipeline resolves the actual Git root for the closed
session. Hook cwd is only a hint; transcript tool workdirs and commit ownership
decide the repo scope so nested repositories do not get attributed to a parent
workspace.

## Production Flow

```text
raw evidence and transcripts
-> closed-session production job
-> reasoning evidence view
-> commit-backed work packets
-> packet-wise reasoning extraction
-> Git hunks and AST CodeNodes
-> reasoning-to-code linking
-> graph validation
-> session trace graph and curated session graph write
-> central_version_merge
-> retrieval docs, embeddings, graph expansion, reranking
```

Production runs this flow through `ProductionSessionJobRunner`. Drain only detects that
a previous session closed and enqueues a job; it does not run extraction or write
graph nodes. Completed stage artifacts are reused unless their input hash or
stage configuration hash changes.

`reasoning evidence view` is scoped by raw hook capture first. Codex rollout
files can contain resumed or forked transcript history, so production does not
scan the whole transcript just because a raw record references `transcript_path`.
When raw evidence contains `turn_id` values, Stage 2 imports only transcript rows
inside matching `task_started` / `task_complete` windows. Whole-transcript
scanning remains a debug-only mode for old reset fixtures and raw captures
without turn ids.

## Source of Truth

The production pipeline uses deterministic facts as the spine:

- Git commits define work boundaries.
- Git hunks define changed code regions.
- AST mapping defines CodeNodes and symbols where possible.
- LLM extraction adds reasoning nodes only after validation.
- Kuzu stores graph truth.
- SQLite stores retrieval/index ledgers.
- FAISS is a rebuildable vector cache.
- SQLite also stores production job state, stage rows, lock leases, retry metadata, and
  the explicit production reset marker.

## Central Merge

The session graph is immutable provenance. It keeps the exact packet, commit,
evidence, hunk, code node, and symbol facts produced by a closed-session
production job.

`central_version_merge` creates the durable canonical layer beside that session
graph:

```text
session Commit/File/Symbol/CodeRegion
-> KnowledgeVersion
-> VERSION_OF
-> KnowledgeAtom
```

The first production implementation applies exact deterministic atoms for
`commit` and `file` by default. `symbol` and `code_region` atoms are created only
when the promotion policy marks a curated ref as a high-signal
`primary_implementation` target. UI style, UI markup, docs, config, validation
tests, and generic support refs stay searchable support by default. Most
symbol/code-region refs therefore remain support metadata so central memory does
not become a second AST dump. Exact matching uses `repo_id` plus canonical keys,
not local machine paths. If a canonical atom already exists, the planner emits it
as `matched_atoms` and still creates a new `KnowledgeVersion` for the new session
provenance.

Apply writes a `GraphCommit`, updates `GraphView(main, active)`, and writes a
`central_version_merge/merge_result.json` sidecar. `merge_plan.json` remains the
dry-run plan; `merge_result.json`, SQLite `v2_graph_commits`, and SQLite
`v2_graph_views` are the applied-state audit trail.

Decision/problem duplicate, refine, supersede, conflict, and revert relations
are review-state central memory. The planner creates review `KnowledgeAtom` and
`KnowledgeVersion` rows for accepted decision/problem frames, and the applier
may write review relation edges such as `DUPLICATE_OF`, `REFINES`,
`SUPERSEDES`, `CONFLICTS_WITH`, or `RELATED_REVIEW`. These edges are audit and
review signals only. They do not make a decision answer-grade, and they do not
change active/refined/superseded/contested status.

The bridge toward that semantic versioning layer is the decision-frame ledger.
Every central merge plan persists accepted decision/problem frames into SQLite
`v2_central_decision_frames`. A later session compares its new frames against
persisted frames and repo-scoped central review versions, then emits review
candidates such as `DUPLICATE_OF` or `REFINES`. This gives AMO a Git-like
history of agent work proposals before the system is trusted to mark old
decisions superseded or contested. The remaining deferred step is automatic
decision status transition, not the storage of decision versions themselves.

Quality evaluation must use the current plan/result pair. If a new
`merge_plan.json` exists beside an older `merge_result.json`, the older result is
treated as stale and cannot make the job product-ready.

Reasoning review also has a deterministic semantic alignment signal. A
structurally valid Qwen node can still be demoted to `needs_review` if its text
does not line up with the commit message or changed files. This catches noisy
packet evidence before it becomes answer-grade graph memory.

## Production Reset

Old graph and retrieval rows are treated as scrap after the production cutover, but
cleanup is never automatic on daemon startup. Operators must run:

```bash
amo-cli reset-production --backup --clean-graph --clean-retrieval
```

New devices with empty production graph/retrieval stores should use normal
install/init instead; `amo-cli init-production` writes the fresh-store marker
without deleting anything.

The command backs up production graph/retrieval/vector stores first, verifies a
backup manifest, then cleans only graph/retrieval/vector/FAISS storage. Raw JSONL
evidence, config, and production job tables are preserved.
The production reset marker is written only after both graph and retrieval
cleanup complete, and the runner refuses production Kuzu/retrieval stages if the
marker is missing, version-mismatched, or incomplete.

Production closed-session processing writes graph node kinds such as `Packet`,
`Commit`, `EvidenceRef`, `ReasoningNode`,
`CodeHunk`, `CodeNode`, `CodeVersion`, and `Symbol`.

Current production writes both an exhaustive trace graph manifest and a curated
session graph manifest. Central merge and retrieval use the curated graph by
default; the trace graph remains an audit/debug artifact. See
[Curated session graph and central merge boundary](./architecture/curated_session_graph.md).

Repo-scoped retrieval projections are cumulative product-memory views. Each
successful `retrieval_docs` stage carries forward previously validated
curated/central docs for the same `repo_id`, adds the current job's curated docs,
deduplicates by document id, and activates a new projection only after the
semantic activation gate passes. This prevents the active repo view from
shrinking to only the latest session while still excluding unscoped `repo_id=""`
or full-trace `CodeNode`/`CodeHunk` docs from product retrieval.

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
4. [Curated session graph boundary](./architecture/curated_session_graph.md)
5. [Node types](./graph_model/node-types.md)
6. [Edge types](./graph_model/edge-types.md)
7. [Provenance and evidence](./graph_model/provenance-and-evidence.md)
8. Algorithm docs under [algorithms](./algorithms/)
9. Module contracts under [modules](./modules/)
10. Examples under [examples](./examples/)

## Acceptance Rule

A graph node is not answer-grade unless it can cite packet, commit, evidence, and when applicable code support. Raw tool calls and internal transcript IDs are provenance only; they are not user-facing reasoning nodes.

## Documentation Contract

Detailed docs in this folder should define inputs, outputs, graph effects, validation checks, and failure modes. Keep user-facing docs focused on product behavior.
