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
- Canonical Phase 1 memory pipeline:
  - raw events
  - typed chunks
  - rule-extracted memory units
  - KG relationships
  - deterministic session summaries
  - pipeline/retrieval/consolidation traces
- Hybrid retrieval:
  - SQLite FTS5 / BM25 lexical search
  - local vector similarity search, with optional FAISS cache
  - KG/entity traversal
  - RRF fusion + local reranking
  - agent-ready context-pack generation with provenance
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
- Hook ingestion entrypoint for Claude/Codex lifecycle events.
- Export pipeline for backup and audit (JSONL snapshots).

## Architecture (high level)

1. Agent emits transcript, hook payload, or tool output.
2. Ingestion adapter normalizes into a canonical event schema.
3. Redaction runs before persistence.
4. Typed chunker splits by content semantics (diff/code/log/stacktrace/prose).
5. Rule extractor creates durable `memory_units` with evidence links.
6. SQLite stores canonical truth; FTS/vector/KG indexes are rebuildable.
7. Retrieval runs BM25/vector/KG, fuses with RRF, reranks, and stores score traces.
8. MCP/CLI/daemon surfaces expose memory and orchestration to agents.

Canonical docs:

- Final architecture baseline: [docs/FINAL_DESIGN_V1.md](./docs/FINAL_DESIGN_V1.md)
- Delivery tracking: [docs/IMPLEMENTATION_TRACKER.md](./docs/IMPLEMENTATION_TRACKER.md)
- Short architecture index: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)

## Quickstart

### One-command install (npx style)

After publishing the npm wrapper package, users can install with:

```bash
npx agent-memory-orchestrator-cli install
```

Then run:

```bash
amo-mcp
```

Phase 1 also installs:

```bash
amo-daemon
amo-hook
```

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

### 3) Run local daemon or MCP server

Daemon:

```bash
amo-daemon
```

Then open:

```text
http://127.0.0.1:8765
```

The local dashboard shows sessions, recent Claude/Codex events, extracted memory units, retrieval queries, score traces, and the memory candidates returned by the system.

MCP server (stdio):

```bash
amo-mcp
```

Local-only defaults are enabled in `.env.example`:

```bash
AMO_LOCAL_ONLY=true
AMO_MCP_TRANSPORT=stdio
AMO_MCP_HOST=127.0.0.1
AMO_APPROVAL_MODE=manual
```

### 4) Optional: ingest transcripts

```bash
amo-cli ingest-transcript --agent claude --file ./sample/claude.jsonl --session-id feature-x
amo-cli ingest-transcript --agent codex --file ./sample/codex.jsonl --session-id feature-x
```

Hook payload ingestion:

```bash
amo-hook --agent codex --file ./sample/codex-hook.json
amo-cli ingest-hook --agent claude --file ./sample/claude-hook.json
```

Inspect/rebuild:

```bash
amo-cli metrics
amo-cli rebuild-indexes --force-vectors
amo-cli session-summary --session-id feature-x
amo-cli search --query "why did retry logic change" --include-historical
amo-cli context-pack --query "why did retry logic change" --format text
```

Import recent Codex sessions as a local memory dataset:

```bash
set AMO_EMBEDDING_MODEL=hash-fallback
amo-cli import-codex-sessions --root %USERPROFILE%\.codex\sessions --limit 5
```

Build a clean test DB from raw Codex sessions without polluting the default DB:

```bash
amo-cli rebuild-clean-db --out .data/clean-codex.db --codex-root %USERPROFILE%\.codex\sessions --limit 30 --force
```

Enable Codex hot-path hooks in `~/.codex/config.toml`:

```toml
[features]
codex_hooks = true

[[hooks.SessionStart]]
matcher = "startup|resume|clear"
[[hooks.SessionStart.hooks]]
type = "command"
command = "python -m agent_memory_orchestrator.hook --agent codex"
timeout = 30
statusMessage = "AMO loading local memory context"

[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "python -m agent_memory_orchestrator.hook --agent codex"
timeout = 30
statusMessage = "AMO retrieving local memory"

[[hooks.PostToolUse]]
matcher = "*"
[[hooks.PostToolUse.hooks]]
type = "command"
command = "python -m agent_memory_orchestrator.hook --agent codex"
timeout = 30
statusMessage = "AMO capturing tool result"

[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "python -m agent_memory_orchestrator.hook --agent codex"
timeout = 30
statusMessage = "AMO summarizing turn"
```

For automatic memory injection into Codex prompts, explicitly opt in:

```bash
set AMO_APPROVAL_MODE=auto_safe
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

## Local model behavior

Memory operations remain offline. Optional model packages can be installed with:

```bash
pip install -e ".[models]"
```

Defaults target:

- Embeddings: `BAAI/bge-m3`
- Reranker: `BAAI/bge-reranker-base`
- Vector cache: FAISS when available

The runtime tries locally available models only. If a model or FAISS is unavailable, AMO falls back to deterministic hash vectors, SQLite vector scan, and lexical reranking rather than making external API calls.

## Publish the npm installer wrapper

The npm installer package is at:

- `npm/agent-memory-orchestrator-cli`

Publish flow:

```bash
cd npm/agent-memory-orchestrator-cli
npm login
npm publish --access public
```

Dry-run pack check:

```bash
npm pack --dry-run
```

## Public repo checklist

- License is MIT.
- No copied AGPL code.
- Keep credentials out of repo (`.env`, `.data`, exports ignored).
- Add CI for lint + tests before publishing.

## Roadmap

- Install optional local model backends:
  - `pip install -e ".[models]"`
  - defaults target `BAAI/bge-m3` and `BAAI/bge-reranker-base`
  - if unavailable, deterministic hash/lexical fallbacks keep tests and offline operation working
- Harden local model packaging and first-run model preflight UX.
- Add Phase 2 Git-like Work Ledger for AI-produced code changes.
- Add app connectors and the private agent hub after the personal memory engine is stable.
- Add policy/rubric scoring for stronger auto-consensus.
- Add web UI for manual memory search and orchestrator approvals.
