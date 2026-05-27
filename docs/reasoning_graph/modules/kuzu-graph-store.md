# Kuzu Graph Store

## Depends on
- ../graph_model/node-types.md
- ../graph_model/edge-types.md
- ../architecture/05-failure-and-safety-model.md

## Used by
- session-timeline-builder.md
- session-graph-builder.md
- central-graph-merge-engine.md
- graph-validation.md

## Related docs
- daemon-job-queue.md

## Purpose

Provide the single graph persistence backend for AMO Reasoning Graph V2.

## Inputs

Graph node/edge upserts, queries, validation traversals, backup/swap operations.

## Outputs

Persisted Kuzu graph and query results.

## Owned state

`AMO_HOME/.graph/amo.kuzu` and rebuild candidate graph paths.

Repo-scoped central graphs live under:

```text
AMO_HOME/.graph/central/<safe_repo_id>/central.kuzu
```

## Public interfaces planned

- `upsert_node(node)`
- `upsert_edge(edge)`
- `list_nodes(filters)`
- `list_edges(filters)`
- `neighbors(node_id)`
- `run_read_query(query)`
- `backup_and_swap(new_graph_path)`

## Kuzu operation modes

Kuzu is embedded. AMO must treat each on-disk graph path as having one of two
process-level modes:

```text
READ_WRITE: exactly one process owns writes for that graph path
READ_ONLY: multiple processes may read the same graph path
```

Kuzu does not allow a read-write `Database` object and another read-only or
read-write `Database` object to query the same on-disk database concurrently.
The official concurrency docs describe the safe choices as one read-write
database object or multiple read-only database objects:
https://kuzudb.github.io/docs/concurrency/

AMO therefore opens Kuzu like this:

```text
daemon graph mutation path -> KuzuGraphStore(path, read_only=False)
retrieval graph expansion -> KuzuGraphStore(path, read_only=True)
central graph display -> KuzuGraphStore(path, read_only=True)
version-flow display -> KuzuGraphStore(path, read_only=True)
offline direct diagnostics -> explicit read-only open unless mutation is requested
```

Read-only stores must not initialize schema or call upsert/status mutation
methods. Schema creation is part of the write path.

## Kuzu writes

All graph writes flow through this module, but domain modules decide what to write.

Normal graph writes are daemon-owned:

```text
hooks capture raw evidence
daemon drains evidence and runs V2 jobs
V2 jobs write session graphs and apply central merges
CLI/MCP/UI call daemon endpoints for writes
```

Direct CLI writes are diagnostic/maintenance paths only. They must be explicit
and should not run concurrently with the daemon graph owner for the same AMO
home.

## Kuzu reads

Read-only graph operations may run from the daemon, CLI, MCP, or UI:

```text
repo-scoped graph retrieval expansion
central graph slices
version-flow queries
answer-trace enrichment
operator diagnostics
```

These paths must use `read_only=True` and must not call `init_schema()`.

Index-only retrieval does not open Kuzu at all. It reads the active
repo-scoped retrieval projection from SQLite and optional FAISS vector cache.

## Failure modes

Lock errors are surfaced, not hidden. A Kuzu lock error means another process
has an incompatible database mode open for the same graph path.

Common causes:

- a read-write maintenance command is running
- a daemon write stage is applying a session graph or central merge
- an old process still holds a direct Kuzu handle
- a read path accidentally opened Kuzu in read-write mode

Expected handling:

```text
index-only retrieval remains available
daemon endpoints should prefer read-only graph expansion
offline direct graph commands may fail fast with lock diagnostics
write stages retry through the job queue rather than racing the lock
```

Backup/swap must be atomic at the active path level. Direct offline opens are
diagnostics only.

## Validation checks

Graph opens through daemon for normal product work. Backup exists before swap.
Node and edge required fields validate before write. Read-only operations can
list nodes, list edges, and traverse neighbors, but cannot mutate graph state.
