# Final Design v1

Status: Approved baseline  
Date: 2026-04-30  
Owner: Agent Memory Orchestrator contributors

## 1) Objective

Build a local-first system that gives Claude and Codex:

- Shared persistent memory across sessions.
- Autonomous memory capture, analysis, and retrieval.
- A deterministic orchestration loop where one agent proposes, the other critiques, and the user is final authority.

Model inference remains with providers (Anthropic/OpenAI). Memory and orchestration stay on device.

## 2) Scope and Boundaries

In scope:

- Local ingestion of Claude/Codex session artifacts.
- Structured memory extraction and storage.
- Hybrid retrieval (lexical + vector).
- Local MCP server for memory and orchestration tools.
- Two-agent review workflow with explicit user approval/rejection.

Out of scope for v1:

- Replacing Claude/Codex hosted inference with local model inference.
- Dependence on third-party orchestrator APIs as core state authority.
- Fully automatic execution without user approval.

## 3) Design Principles

- Local-first by default.
- Provider-agnostic interfaces (adapters for Claude/Codex).
- Auditability of every memory write and orchestration transition.
- Deterministic state machine for orchestration.
- Replaceable storage/retrieval backends behind interfaces.

## 4) System Architecture

Core modules:

1. `ingestion_gateway`
- Accepts transcript events from Claude/Codex adapters.
- Normalizes to canonical `Event` schema.

2. `memory_pipeline`
- Extracts candidate memories from events.
- Generates tags, importance, and optional confidence.
- Creates vector representation.

3. `memory_store`
- Persists sessions, events, memories, vectors, and retrieval metadata.
- Default backend: SQLite (single-node local).
- Future backend options: Postgres/pgvector.

4. `retrieval_engine`
- Executes lexical recall + vector similarity + rerank.
- Returns compact context packets for agent prompts.

5. `mcp_memory_server`
- Exposes memory and orchestrator tools via MCP.
- Shared endpoint consumed by both Claude and Codex runtimes.

6. `orchestrator_core`
- Maintains state machine and round artifacts.
- Enforces critique/revise loop and consensus threshold.

7. `approval_gateway`
- Captures user final decision and closes session state.

8. `omnara_adapter` (optional)
- Bridges external orchestration visibility/control.
- Never becomes source of truth for authoritative state.

## 5) Canonical Data Model

Primary entities:

- `sessions`: logical task thread.
- `events`: normalized raw interaction units.
- `memories`: extracted durable facts/decisions.
- `memory_vectors`: semantic vectors for memories.
- `orchestration_rounds`: each Claude/Codex review turn.
- `orchestration_decisions`: user final approval/rejection.

`events` minimum fields:

- `id`
- `session_id`
- `agent` (`claude` | `codex` | `system` | `user`)
- `event_type` (`prompt`, `response`, `tool_call`, `tool_result`, `decision`, ...)
- `content`
- `metadata_json`
- `created_at`

`memories` minimum fields:

- `id`
- `session_id`
- `source_event_id`
- `summary`
- `tags_json`
- `importance`
- `created_at`

## 6) Memory Lifecycle

1. Ingest event.
2. Normalize + validate schema.
3. Extract memory candidate(s).
4. Embed memory text.
5. Persist memory + vector.
6. Update retrieval index metadata.
7. Emit audit log record.

Autonomous export:

- Time-based snapshot export (`N` minutes).
- Session-close export.
- Optional manual export command.

## 7) Retrieval Contract

Query flow:

1. Apply filters (`session_id`, `agent`, `time_range`, `tags`).
2. Retrieve lexical candidates (SQLite text match / FTS).
3. Retrieve vector candidates (cosine similarity).
4. Merge and rerank.
5. Return top `k` memories + provenance.

Return payload includes:

- `memory_id`
- `summary`
- `score`
- `source_event_id`
- `session_id`
- `created_at`

## 8) Orchestrator Protocol

### 8.1 States

- `draft`
- `review`
- `revise`
- `ready_for_user`
- `approved`
- `rejected`

### 8.2 Transition Rules

- `draft -> review`: Claude submits plan/design artifact.
- `review -> revise`: Codex flags blockers or low confidence.
- `review -> ready_for_user`: No blockers and confidence threshold met.
- `revise -> review`: Claude submits updated artifact.
- `ready_for_user -> approved|rejected`: user decision only.

### 8.3 Consensus Rules (v1)

- Codex can mark `blocking_issues`.
- If `blocking_issues` not empty, must loop to `revise`.
- If both agents pass threshold and no blockers, can ask user.
- Maximum rounds guard: `AMO_MAX_REVIEW_ROUNDS` (default 5).

## 9) MCP Tool Surface (v1)

Memory tools:

- `memory_write`
- `memory_search`
- `memory_timeline`
- `memory_export`
- `memory_import`

Orchestrator tools:

- `orchestrator_start`
- `orchestrator_submit`
- `orchestrator_status`
- `orchestrator_user_decision`
- `orchestrator_history`

Admin/health:

- `health_ping`
- `config_view`

## 10) Local Deployment Topology

Single-device topology:

- `amo-mcp` process (stdio or local port).
- SQLite DB in `.data/`.
- Optional local vector service (Qdrant/Chroma) behind interface.
- Optional adapter process for external visibility.

No external call path for memory/orchestration operations.

## 11) Security and Privacy Baseline

- Secrets via `.env`, never committed.
- Memory export files ignored by git.
- Local-only bind address (`127.0.0.1`) when using TCP transport.
- Redaction hook for secrets before persistence (planned early milestone).

## 12) Observability and Audit

- Append-only event log for ingest/write/search/orchestrator transitions.
- Per-session trace identifiers.
- Replay capability from exported JSONL snapshots.

## 13) Decisions Locked for v1

1. Authoritative state is local DB, not external orchestrator.
2. MCP is the integration boundary for both agents.
3. SQLite-first implementation before distributed DB complexity.
4. Orchestrator requires explicit user final decision.
5. Omnara integration is optional and adapter-only.

## 14) v1 Exit Criteria

All must pass:

1. Claude and Codex can both read/write same memory through MCP.
2. Memory persists across restarts and across sessions.
3. Orchestration loop blocks finalization on Codex blockers.
4. User can approve/reject and decision is persisted.
5. Memory search returns relevant cross-session context with provenance.

