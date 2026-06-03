# Runtime Ownership

## Depends on
- 02-three-level-storage.md
- 05-failure-and-safety-model.md

## Used by
- ../modules/daemon-job-queue.md
- ../modules/qwen-contracts.md

## Related docs
- ../modules/kuzu-graph-store.md
- ../modules/raw-evidence-ledger.md
- ../modules/session-graph-builder.md

## Ownership Rule

Hooks capture. The daemon processes. CLI/API/MCP control and inspect.

This split is mandatory because Qwen, embeddings, Tree-sitter parsing, Kuzu writes, clustering, and central merge can be slow or fail. Hooks must not block agent work.

## Hook Responsibilities

Hooks may:

- Receive `SessionStart`, `UserPromptSubmit`, `PostToolUse`, and `Stop` payloads.
- Redact obvious secrets.
- Append raw evidence to `AMO_HOME/.evidence` or fallback spool.
- Return capture status.

Hooks must not:

- Open Kuzu.
- Call Qwen.
- Compute embeddings.
- Run Tree-sitter.
- Merge central graph nodes.
- Rebuild indexes.

## Daemon Responsibilities

The daemon owns:

- Kuzu database connection.
- Evidence drain cursors.
- Job queue.
- Codex transcript import.
- Timeline construction.
- Chunking and session graph build.
- Qwen calls.
- Embedding calls.
- Git diff/code analysis.
- Central graph merge.
- Dependency propagation.
- Leiden clustering.
- Graph validation.
- Web/API diagnostics.

## CLI/API/MCP Responsibilities

CLI, API, and MCP should call daemon endpoints by default. They should not silently open Kuzu if the daemon is unavailable, because embedded graph locks can corrupt user expectations and create inconsistent reads.

Offline diagnostic commands can exist, but they must clearly state when they are
opening Kuzu directly and may fail on locks.

Graph-expanded reads must use read-only Kuzu handles. Index-only retrieval can
read SQLite/FAISS without opening Kuzu. Direct read-write Kuzu opens outside the
daemon are maintenance operations, not normal retrieval.

## Daemon Ownership Lock

Only one daemon process may own graph writes for a given AMO home. In-process
locks serialize multiple jobs inside one daemon, but they do not coordinate two
separate OS processes. The daemon therefore holds
`.state/daemon-owner.lock` for its whole lifetime before auto-drain starts or
Kuzu is opened for graph writes.

If a second daemon starts for the same AMO home, it must exit before starting
auto-drain. Dashboard/API readers should use SQLite, retrieval projections, and
production artifacts by default; explicit graph expansion can still fail fast if Kuzu is
locked by the graph owner.

## Kuzu Access Matrix

| Operation | Normal owner | Kuzu mode |
| --- | --- | --- |
| Hook capture | Hook process | none |
| Session graph write | Daemon job runner | read-write |
| Central merge apply | Daemon job runner | read-write |
| Retrieval candidate search | CLI/API/MCP/UI | none, SQLite/FAISS only |
| Retrieval graph expansion | Daemon/API/CLI diagnostic | read-only |
| Central graph display | Daemon/API/CLI diagnostic | read-only |
| Version-flow display | Daemon/API/CLI diagnostic | read-only |
| Graph cleanup/consolidate with apply | Daemon maintenance path | read-write |
| Direct offline mutation | Operator diagnostic | read-write, explicit only |

Repo-scoped product graph reads must use the repo central graph path:

```text
AMO_HOME/.graph/central/<safe_repo_id>/central.kuzu
```

They should not expand against the global `AMO_HOME/.graph/amo.kuzu` unless the
query is explicitly global/debug. The global graph can still support legacy
diagnostics and non-repo views, but active repo memory is resolved through the
repo central graph and the active SQLite `GraphView`.

## Job Queue Rule

Stop/finalize should enqueue graph processing work. The queue must be idempotent by session id, evidence id range, extraction run id, and commit id. Retrying a job must not duplicate graph nodes.
