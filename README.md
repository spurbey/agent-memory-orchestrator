# Agent Memory Orchestrator

Public-ready reference implementation for:
- Shared persistent memory across multiple coding agents (Claude + Codex).
- Autonomous memory extraction and retrieval.
- Local MCP tools for both agents.
- A review orchestrator loop (Claude drafts, Codex critiques, user approves).

## Why this exists

When you run many parallel AI sessions, context gets fragmented and lost. This project keeps a single memory timeline and enforces a clear multi-agent decision workflow before execution.

## Core capabilities

- Persistent session and event store (SQLite).
- Automatic memory extraction from raw events.
- Hybrid retrieval:
  - lexical search (keyword/tags/time)
  - vector similarity search (local deterministic embedding baseline)
- Local MCP server tools:
  - `memory_write`
  - `memory_search`
  - `memory_timeline`
  - `orchestrator_start`
  - `orchestrator_submit`
  - `orchestrator_status`
  - `orchestrator_user_decision`
- Orchestrator state machine:
  - `draft -> review -> revise (loop) -> ready_for_user -> approved/rejected`
- Transcript ingestion adapters for Claude/Codex JSONL.
- Export pipeline for backup and audit (JSONL snapshots).

## Architecture (high level)

1. Agent emits transcript/tool output.
2. Ingestion adapter normalizes into a common event schema.
3. Memory worker extracts observation summaries + tags + embedding.
4. Data persists to SQLite (`events`, `memories`, `vectors`).
5. MCP tools expose retrieval and orchestration actions to Claude/Codex.
6. Orchestrator enforces two-agent review + explicit user approval.

Canonical docs:

- Final architecture baseline: [docs/FINAL_DESIGN_V1.md](./docs/FINAL_DESIGN_V1.md)
- Delivery tracking: [docs/IMPLEMENTATION_TRACKER.md](./docs/IMPLEMENTATION_TRACKER.md)
- Short architecture index: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)

## Quickstart

### 1) Create environment

```bash
python -m venv .venv
. .venv/bin/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2) Initialize DB

```bash
amo-cli init-db
```

### 3) Run MCP server (stdio)

```bash
amo-mcp
```

Local-only defaults are enabled in `.env.example`:

```bash
AMO_LOCAL_ONLY=true
AMO_MCP_TRANSPORT=stdio
AMO_MCP_HOST=127.0.0.1
```

### 4) Optional: ingest transcripts

```bash
amo-cli ingest-transcript --agent claude --file ./sample/claude.jsonl --session-id feature-x
amo-cli ingest-transcript --agent codex --file ./sample/codex.jsonl --session-id feature-x
```

### 5) Export memory snapshot

```bash
amo-cli export --out ./exports/memory_snapshot.jsonl
```

## MCP client wiring

Point Claude/Codex MCP configuration to run:

```bash
python -m agent_memory_orchestrator.mcp_server
```

Both agents then operate on the same local memory and orchestration state.

## Public repo checklist

- License is MIT.
- No copied AGPL code.
- Keep credentials out of repo (`.env`, `.data`, exports ignored).
- Add CI for lint + tests before publishing.

## Roadmap

- Replace local baseline embeddings with hosted/local embedding model.
- Plug a real vector DB (Qdrant/Chroma/pgvector) behind `VectorStore` interface.
- Add policy/rubric scoring for stronger auto-consensus.
- Add web UI for manual memory search and orchestrator approvals.
