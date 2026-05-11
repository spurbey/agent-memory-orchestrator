# Final Design v1

Status: GraphRAG V1 finalization in progress
Date: 2026-05-10
Owner: Agent Memory Orchestrator contributors

## Canonical Detailed Spec

The detailed implementation authority for Reasoning Graph V1 is
`docs/reasoning_graph/`. This file remains the high-level design index. If this
file conflicts with the detailed module, algorithm, graph-model, or phase docs
under `docs/reasoning_graph/`, follow the detailed docs and update this index.

## 1) Objective

Build a local-first memory brain for Claude, Codex, and future apps where:

- Hooks capture raw evidence without heavy processing or prompt pollution.
- `amo-daemon` owns Kuzu, Qwen/Ollama, graph jobs, retrieval, and merge jobs.
- One central personal graph links sessions, repos, commits, files, decisions, fixes, tests, and raw evidence refs.
- Local Git remains code truth; AMO stores reasoning and provenance above Git.
- Agents retrieve memory explicitly through MCP tools when the user or agent asks for it.

## 2) Scope and Boundaries

In scope:

- Capture-only Claude/Codex hooks.
- Append-only raw evidence storage with hashes and file offsets.
- Daemon-owned embedded Kuzu graph.
- Session draft graph and rolling `ContextSnapshot` built only after meaningful write/test/git/finalize triggers.
- Qwen via Ollama for bounded GraphDelta extraction, query planning, merge classification, and context compression.
- Local Git work ledger linking `Decision -> WorkChange -> File/TestRun -> GitCommit`.
- Explicit MCP tools for GraphRAG retrieval and diagnostics.

Out of scope for this phase:

- Hosted sync or team graph sharing.
- Replacing Claude/Codex inference.
- Migrating old noisy SQLite memory rows into the new graph by default.
- Heavy model work inside hooks.

## 3) Design Principles

- Daemon owns stateful graph/model resources.
- Hooks fail open and must stay fast.
- Raw evidence is evidence, not memory.
- Graph writes are non-destructive: supersession/refinement/merge decisions are edges and statuses.
- Retrieval is explicit by MCP/daemon, not automatic on every prompt.
- Git anchors code truth; AMO anchors reasoning and work provenance.

## 4) Runtime Architecture

1. `amo-hook`
- Parses hook payload.
- Appends redacted raw evidence to `AMO_HOME/.evidence`.
- Falls back to workspace `.amo-spool/evidence` if AMO home is blocked.
- Returns startup status only on `SessionStart`.
- Does not call Kuzu, Qwen, retrieval, or graph writes.

2. `amo-daemon`
- Owns Kuzu and all graph writes.
- Drains evidence with durable cursors and idempotent hashes.
- Detects write/test/git/finalize triggers.
- Calls Qwen only for bounded trigger windows.
- Updates session graph, `ContextSnapshot`, and commit-linked work ledger.
- Serves GraphRAG APIs and diagnostics.

3. `amo-mcp`
- Exposes explicit graph tools.
- Delegates GraphRAG calls to daemon by default.
- If daemon is down, returns `requires_daemon=true` instead of opening Kuzu directly.

4. Hybrid SQLite memory pipeline
- Remains available as a BM25/vector/KG retrieval source.
- Does not power capture hooks or automatic prompt injection.

## 5) Graph Model

Core nodes:

- `User`, `Agent`, `App`, `Project`, `Repo`, `Branch`, `GitCommit`
- `Session`, `Turn`, `Prompt`, `Response`, `ToolUse`, `ToolResult`
- `WorkChange`, `Decision`, `Bug`, `Fix`, `Blocker`, `TestRun`
- `File`, `Symbol`, `Topic`, `ContextSnapshot`, `RawEvidenceRef`

Core edges:

- `PART_OF`, `HAS_TURN`, `ASKED`, `ANSWERED`, `USED_TOOL`, `PRODUCED`
- `TOUCHES`, `MODIFIES`, `IMPLEMENTS`, `FIXES`, `VALIDATED_BY`
- `ABOUT`, `EVIDENCED_BY`, `SUPERSEDES`, `REFINES`, `CONTRADICTS`
- `DEPENDS_ON`, `MERGED_INTO`, `COMMITTED_AS`

Statuses:

- `draft`, `committed`, `active`, `superseded`, `contested`, `abandoned`

## 6) Session Lifecycle

1. Session starts.
- Hook records `SessionStart` raw evidence.
- Daemon creates/updates session, app, repo, branch, and raw evidence refs when drained.
- Hook may inject only tiny startup status.

2. Discussion/read-only work happens.
- Hooks capture prompts/tool results as raw evidence.
- Daemon stores evidence refs and lightweight event nodes.
- No Qwen extraction and no session context update unless a trigger appears.

3. Write/test/git/finalize trigger appears.
- Daemon collects evidence since last checkpoint.
- Qwen extracts a validated `GraphDelta`.
- Daemon creates/updates draft `WorkChange`, `Decision`, `Bug`, `Fix`, `TestRun`, `File`, and latest `ContextSnapshot`.

4. Git commit happens.
- Local Git backend reads commit metadata, diff summary, changed files, patch-id, branch, and repo.
- Daemon runs `CommitMergeEngine` on the session draft graph.
- Answer-grade draft nodes are promoted to central committed nodes.
- Support/raw nodes stay as provenance and are never promoted as answer-grade memory.
- Deterministic scoring classifies obvious duplicate/refine/supersede/contradict relations.
- Ambiguous relations are sent to Qwen when available; low-confidence results are reported for review and do not mutate the central graph.
- Versioning is stored as non-destructive edges: `COMMITTED_AS`, `REFINES`, `SUPERSEDES`, `DUPLICATE_OF`, `CONTRADICTS`, `VALIDATED_BY`, and `MODIFIES`.

5. Rebuild/repair happens.
- `graph-rebuild-central --from-evidence` previews all raw evidence roots and rebuild targets.
- `graph-rebuild-central --from-evidence --backup-current --apply` rebuilds from raw evidence with current cleaning/extraction gates, consolidates the new graph, validates it, backs up the old graph, swaps the rebuilt graph into place, and rebuilds the retrieval cache.

## 7) Retrieval Contract

Explicit MCP/daemon retrieval pipeline:

1. Qwen query planner classifies intent.
2. Seed retrieval uses Kuzu text search and graph node filters.
3. Graph expansion follows nearby edges.
4. Ranking applies deterministic product policy:
- committed/active over draft/superseded,
- evidence-backed over loose summaries,
- answer-grade nodes over raw evidence,
- raw payloads only through `amo_raw_evidence`.
5. Qwen compresses selected graph nodes into clean agent context.

Current implementation includes deterministic ranking and Qwen planner/compressor fallback. BM25/FAISS/BGE cross-encoder caches remain planned as rebuildable derived indexes, not graph truth.

## 8) MCP Tool Surface

GraphRAG tools:

- `amo_graph_search`
- `amo_current_context`
- `amo_decision_history`
- `amo_work_history`
- `amo_raw_evidence`
- `amo_merge_status`

Hybrid memory compatibility tools:

- `memory_write`
- `memory_search`
- `memory_context_pack`
- `memory_timeline`
- `memory_export`
- `memory_import`

Implementation rule:

- `mcp/server.py` registers thin wrappers.
- `mcp/tools.py` implements `MemoryMcpToolService` contracts.
- Graph tools call daemon by default and report daemon unavailability clearly.

## 9) Debuggability

Every stage should be inspectable:

- `amo-cli debug hooks`: config, hook log, latest evidence.
- `amo-cli debug drain`: cursor and pending evidence.
- `amo-cli debug qwen`: local model JSON/latency check.
- `amo-cli debug graph`: graph status and latest session context.
- `amo-cli debug retrieval`: daemon retrieval output and timing.
- `amo-cli graph-finalize-session --session-id <id> --commit <sha|HEAD>`: dry-run merge plan.
- `amo-cli graph-finalize-session --session-id <id> --commit <sha|HEAD> --apply`: promote draft session work into the central graph.
- `amo-cli graph-rebuild-central --from-evidence --backup-current`: dry-run rebuild plan.
- `amo-cli graph-rebuild-central --from-evidence --backup-current --apply`: backup/replay/swap the active graph.

Latency targets:

- Hook: target under 500ms, fail open before configured timeout.
- Drain without Qwen: under 1s per batch.
- Qwen extraction: target under 30s per bounded write window.
- Explicit retrieval: target under 10s warm local model.

## 10) Locked Decisions

1. Kuzu is graph truth.
2. SQLite is legacy/compatibility only for this pivot.
3. Qwen via Ollama is the default local LLM runtime.
4. `qwen3:1.7b` is the reliable default; larger Qwen models are opt-in.
5. Hooks capture only; retrieval is explicit except tiny `SessionStart` status.
6. Raw evidence is not included in context unless explicitly requested.
7. Local Git ships first behind a version backend interface.
8. Existing noisy SQLite memory rows are not migrated into Kuzu by default.

## 11) Exit Criteria

- Hook capture works without Kuzu/Qwen and never hangs Codex.
- Daemon drains evidence idempotently into Kuzu.
- Read-only prompts do not trigger Qwen or context snapshots.
- Write/test/git/finalize events produce session graph and `ContextSnapshot`.
- Commit/finalize events promote answer-grade session work into central committed graph nodes.
- Version edges preserve duplicate/refine/supersede/contradict history without deleting old nodes.
- Rebuild can replay raw evidence into a fresh graph and swap only after validation.
- MCP GraphRAG tools retrieve from daemon and never silently fall back to legacy SQLite.
- Debug commands identify hook, drain, Qwen, graph, retrieval, and latency failures.
