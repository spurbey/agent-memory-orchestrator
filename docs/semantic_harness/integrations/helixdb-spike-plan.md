# HelixDB Cutover And Traversal Plan

## Purpose

Run Semantic Harness graph persistence and query loading on local HelixDB, then
incrementally move relationship and vector operations behind the same planner
contracts.

## Policy

HelixDB is authoritative for the Semantic Harness graph. The AMO planner remains
backend-neutral and owns traversal policy; infrastructure adapters execute plans
but do not decide product behavior.

## Inputs

- structural graph snapshot
- projection documents
- selected semantic-enriched relation reasons when available
- query mode fixtures

## Current Cutover

```text
MCP context_for_anchor graph loading -> HelixDB
proxy rank_tool_hits graph loading -> HelixDB
bootstrap and shadow replay persistence -> HelixDB
semantic checkpoint ingest and attach -> HelixDB
legacy SQLite graph -> explicit one-time migration command
projection generation -> deterministic in-process rebuild from Helix graph
```

## Local Runtime

HelixDB runs as a local service. AMO does not require its own Docker container,
but the local Helix instance must be running before harness bootstrap, MCP
queries, checkpoint attach, or proxy ranking.

```text
default URL: http://127.0.0.1:6969
environment override: AMO_HELIX_URL
batch-size override: AMO_HELIX_BATCH_SIZE
health endpoint: GET /health
```

The current local project is managed by the Helix CLI and Docker Desktop. Disk
persistence belongs to the Helix instance, not AMO's SQLite files. A missing or
stopped service is an operational unavailable state; it must not silently fall
back to SQLite.

One-time migration:

```powershell
amo-cli amo-harness migrate-sqlite-to-helix `
  --repo-id <repo-id> `
  --db-path "$HOME/.agent-memory-orchestrator/.data/semantic_harness.sqlite"
```

Migration passes only when source and target node count, edge count, and
structural graph snapshot ID are identical.

## Follow-Up Scenarios

```text
context_for_anchor
rank_tool_hits
relationship_between_anchors
pre_edit_review structural v1
history_for_anchor when version data exists
```

## Evaluation

Compare native Helix traversal/vector execution against the current
Helix-load-plus-domain-algorithm path:

- output quality
- latency
- query complexity
- ability to combine graph/text/vector operations
- debugging clarity

## Constraints

- No arbitrary LLM-authored Helix queries.
- No mode-specific traversal policy inside the Helix adapter.
- Same Query IR must drive in-process and native Helix execution.
- SQLite must not return as a production harness query fallback.
