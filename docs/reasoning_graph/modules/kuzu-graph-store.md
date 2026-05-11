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
- ../implementation/07-phase-fresh-rebuild.md

## Purpose

Provide the single graph persistence backend for AMO Reasoning Graph V1.

## Inputs

Graph node/edge upserts, queries, validation traversals, backup/swap operations.

## Outputs

Persisted Kuzu graph and query results.

## Owned state

`AMO_HOME/.graph/amo.kuzu` and rebuild candidate graph paths.

## Public interfaces planned

- `upsert_node(node)`
- `upsert_edge(edge)`
- `list_nodes(filters)`
- `list_edges(filters)`
- `neighbors(node_id)`
- `run_read_query(query)`
- `backup_and_swap(new_graph_path)`

## Kuzu writes

All graph writes flow through this module, but domain modules decide what to write.

## Failure modes

Lock errors are surfaced, not hidden. Backup/swap must be atomic at the active path level. Direct offline opens are diagnostics only.

## Validation checks

Graph opens through daemon. Backup exists before swap. Node and edge required fields validate before write.