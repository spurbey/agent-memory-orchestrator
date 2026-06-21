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
MCP context_for_anchor anchor/fact/one-hop reads -> native Helix traversal
proxy rank_tool_hits candidate file/symbol reads -> native Helix traversal
bootstrap and shadow replay persistence -> HelixDB
semantic checkpoint ingest and attach -> HelixDB
legacy SQLite graph -> explicit one-time migration command
projection generation -> deterministic rebuild from bounded evidence subgraph
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

The local project is managed by the Helix CLI and Docker. Disk
persistence belongs to the Helix instance, not AMO's SQLite files. A missing or
stopped service is an operational unavailable state; it must not silently fall
back to SQLite.

One-command user setup:

```powershell
amo-cli amo-harness setup --repo .
```

This command installs the pinned Helix CLI when missing, verifies Docker,
initializes and starts a disk-backed local instance, then safely chooses one
graph action: reuse an existing graph, migrate a legacy graph, or bootstrap a
new graph. Existing graphs are never replaced without `--rebuild` or the
bootstrap command's `--replace` flag.

One-time migration:

```powershell
amo-cli amo-harness migrate-sqlite-to-helix `
  --repo-id <repo-id> `
  --db-path "$HOME/.agent-memory-orchestrator/.data/semantic_harness.sqlite"
```

Migration passes only when source and target node count, edge count, structural
snapshot ID, and full graph-content digest are identical. The content digest
includes semantic metadata, summaries, relation weights, confidence, and edge
metadata.

## Follow-Up Scenarios

```text
relationship_between_anchors
pre_edit_review structural v1
history_for_anchor when version data exists
```

## Evaluation

Compare future native Helix path/vector execution against the current
bounded-slice-plus-domain-algorithm path:

- output quality
- latency
- query complexity
- ability to combine graph/text/vector operations
- debugging clarity

Initial migrated-graph canary (`15,682` nodes, `31,324` edges):

```text
context_for_anchor native slice: about 2.0 seconds
complete graph reconstruction:  about 13.0 seconds
answer/status equality:          exact for the fixture
100 candidate rank slice:        3 Helix requests, 1,051 returned nodes
```

## Constraints

- No arbitrary LLM-authored Helix queries.
- No mode-specific traversal policy inside the Helix adapter.
- Same Query IR must drive in-process and native Helix execution.
- SQLite must not return as a production harness query fallback.
