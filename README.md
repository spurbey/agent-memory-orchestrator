# Agent Memory Orchestrator

Public-ready reference implementation for:
- Local Kuzu GraphRAG memory across coding agents (Claude + Codex first).
- Capture-only hooks that record evidence without polluting every prompt.
- Explicit MCP retrieval tools for agents/users when memory is actually needed.
- Git-linked work history so decisions, file changes, tests, and commits stay connected.

## Why this exists

When you run many parallel AI sessions, context gets fragmented and lost. This project keeps a single memory timeline and enforces a clear multi-agent decision workflow before execution.

## Core capabilities

- Kuzu graph database as the primary memory brain.
- Raw hook/transcript events stored as append-only evidence refs, not injected memory.
- Per-session draft graph linked to repo, branch, prompts, responses, tool events, and raw evidence.
- Local Git backend links work graph nodes to commits.
- Qwen via Ollama is the required local LLM runtime for GraphRAG planning, extraction, merging, and context compression.
- Legacy SQLite memory pipeline remains available for compatibility/debugging, but is not the new hook or GraphRAG path.
- Local MCP server tools:
  - `amo_graph_search`
  - `amo_current_context`
  - `amo_decision_history`
  - `amo_work_history`
  - `amo_raw_evidence`
  - `amo_merge_status`
  - legacy: `memory_write`
  - legacy: `memory_search`
  - legacy: `memory_context_pack`
  - legacy: `memory_timeline`
  - legacy: `memory_export`
  - legacy: `memory_import`
- Orchestrator state machine:
  - `draft -> review -> revise (loop) -> ready_for_user -> approved/rejected`
- Transcript ingestion adapters for Claude/Codex JSONL.
- Hook capture entrypoint for Claude/Codex lifecycle events.
- Modular adapter layer for Codex, Claude, and optional non-authoritative Omnara visibility events.
- Export pipeline for backup and audit (JSONL snapshots).

## Legacy SQLite tools

The original Phase 1 memory tools are still present for compatibility:

  - `memory_write`
  - `memory_search`
  - `memory_context_pack`
  - `memory_timeline`
  - `memory_export`
  - `memory_import`

They should not power automatic prompt injection in the new architecture.

## Architecture (high level)

1. Claude/Codex emits hook payloads, transcript events, or tool output.
2. `amo-hook` captures the payload as raw evidence and fails open if graph runtime is unavailable.
3. `amo-daemon` owns Kuzu, Qwen/Ollama, graph jobs, Git snapshots, and explicit GraphRAG retrieval.
4. Kuzu stores a personal graph across sessions, apps, repos, commits, decisions, work changes, and evidence refs.
5. `UserPromptSubmit` is capture-only; it does not auto-retrieve memory.
6. `SessionStart` may inject only a tiny startup status saying AMO GraphRAG is active.
7. Claude/Codex call explicit MCP tools such as `amo_graph_search` when memory is needed.
8. On Git commit, daemon links session graph nodes to the commit and merges them into the central personal graph.

Canonical docs:

- Final architecture baseline: [docs/FINAL_DESIGN_V1.md](./docs/FINAL_DESIGN_V1.md)
- Delivery tracking: [docs/IMPLEMENTATION_TRACKER.md](./docs/IMPLEMENTATION_TRACKER.md)
- Short architecture index: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)

## Quickstart

### One-command install (npx style)

Install the Python runtime, write local AMO config, register Claude/Codex hooks + MCP, and initialize local stores:

```bash
npx agent-memory-orchestrator-cli install
```

Target one agent if preferred:

```bash
npx agent-memory-orchestrator-cli install --target codex
npx agent-memory-orchestrator-cli install --target claude
```

Select local model profile during install:

```bash
npx agent-memory-orchestrator-cli install --preset cpu-balanced --download-models
```

The installer previews changes and backs up agent config files before writing. It configures capture-only hooks to call `amo-hook` and MCP to call `amo-mcp` through the selected AMO home directory.

Kuzu is embedded; no Neo4j/Docker/server process is required. Qwen runs through local Ollama:

```bash
ollama pull qwen3:1.7b
```

Diagnostics:

```bash
amo-cli doctor
```

### 1) Create environment

```bash
python -m venv .venv
. .venv/bin/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2) Initialize stores

```bash
amo-cli init-db
amo-cli init-graph
```

### 3) Run local daemon or MCP server

Daemon:

```bash
amo-daemon
```

Installed Codex/Claude setup:

```powershell
python -m agent_memory_orchestrator.daemon --amo-home "$env:USERPROFILE\.agent-memory-orchestrator"
```

Then open:

```text
http://127.0.0.1:8765
```

The local dashboard shows local AMO state. The new GraphRAG APIs are explicit; hook prompt submission does not retrieve memory automatically.

Graph visualization:

```text
http://127.0.0.1:8765/graph
```

The legacy graph view still renders the old SQLite KG/debug data. The new Kuzu-backed graph APIs are exposed through MCP/daemon endpoints and will replace this UI surface.

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

## Slack Connector: local Socket Mode

This connector is local-first. Slack delivers events through an outbound WebSocket opened by your machine, so AMO does not expose localhost to the internet and does not need a hosted relay.

What it does:

- Captures relevant sent Slack messages as append-only raw evidence under `AMO_HOME/.evidence`.
- Groups Slack messages into AMO sessions by `team/channel/thread`.
- Replies only when the bot is explicitly mentioned.
- Writes a connector-finalize event when a Slack session should be summarized.
- Lets normal `graph-drain` convert the cleaned Slack evidence window into graph nodes.

What it does not do:

- It cannot capture unsent typing drafts. Slack only emits messages after they are sent.
- It does not store tokens in the repo.
- It does not open a public webhook server.

Lowest-friction setup:

```powershell
$env:AMO_HOME="$env:USERPROFILE\.agent-memory-orchestrator"
python -m agent_memory_orchestrator.cli slack setup-link
```

Open the printed URL. Slack opens the create-app flow with AMO's manifest prefilled, including Socket Mode, bot scopes, and event subscriptions. The user only selects the workspace, reviews, and creates the app.

If the user generated a temporary Slack App Configuration Token from the Slack apps page, AMO can create the app through Slack's Manifest API:

```powershell
python -m agent_memory_orchestrator.cli slack bootstrap --config-token "xoxe..."
```

The command returns the new `app_id` and an `oauth_authorize_url` when Slack provides one. Open that URL to approve installation. Slack still controls final workspace approval and token issuance.

Manual fallback: generate the Slack app manifest:

```powershell
python -m agent_memory_orchestrator.cli slack manifest --out .\slack-app-manifest.json
```

Create a Slack app from that manifest, then create:

- Bot token: `xoxb-...`
- App-level Socket Mode token: `xapp-...`

After you have both tokens, use the interactive local setup wizard:

```powershell
python -m agent_memory_orchestrator.cli slack setup-wizard
```

The wizard asks you to paste the `xapp` and `xoxb` tokens, validates them with Slack by default, derives `team_id` and `bot_user_id` when possible, and saves tokens locally under `AMO_HOME/.secrets/slack.json` unless you choose otherwise.

Non-interactive fallback: configure AMO without saving tokens:

```powershell
$env:AMO_SLACK_APP_TOKEN="xapp-..."
$env:AMO_SLACK_BOT_TOKEN="xoxb-..."
python -m agent_memory_orchestrator.cli slack setup `
  --team-id T123 `
  --bot-user-id B123 `
  --capture-user-id U123 `
  --skip-token-validation
```

Or save tokens locally under `AMO_HOME/.secrets/slack.json`:

```powershell
python -m agent_memory_orchestrator.cli slack setup `
  --team-id T123 `
  --bot-user-id B123 `
  --capture-user-id U123 `
  --app-token "xapp-..." `
  --bot-token "xoxb-..." `
  --save-tokens `
  --skip-token-validation
```

Install the optional WebSocket runtime and run the connector:

```powershell
pip install -e ".[slack]"
python -m agent_memory_orchestrator.cli slack run --reply-mode disabled
```

Finalize a Slack session and drain it into the graph:

```powershell
python -m agent_memory_orchestrator.cli slack finalize-session --session-id "slack:T123:C123:1710000000.000100"
python -m agent_memory_orchestrator.cli graph-drain --session-id "slack:T123:C123:1710000000.000100" --limit 100
```

Module layout:

```text
src/agent_memory_orchestrator/connectors/
  base.py                         # connector event contract
  slack/
    config.py                     # local config, env, token storage paths
    manifest.py                   # Slack app manifest generator
    client.py                     # minimal Slack Web API client
    events.py                     # Socket Mode event normalization/routing rules
    service.py                    # evidence capture, setup, status, finalize
    socket_mode.py                # outbound WebSocket runner
```

### 4) Optional: ingest transcripts

```bash
amo-cli ingest-transcript --agent claude --file ./sample/claude.jsonl --session-id feature-x
amo-cli ingest-transcript --agent codex --file ./sample/codex.jsonl --session-id feature-x
```

Hook payload capture:

```bash
amo-hook --agent codex --file ./sample/codex-hook.json
amo-cli ingest-hook --agent claude --file ./sample/claude-hook.json
```

Legacy inspect/rebuild:

```bash
amo-cli metrics
amo-cli rebuild-indexes --force-vectors
amo-cli session-summary --session-id feature-x
amo-cli search --query "why did retry logic change" --include-historical
amo-cli context-pack --query "why did retry logic change" --format text
```

Do not use legacy context-pack as automatic hook injection in the Kuzu architecture. Use explicit MCP GraphRAG tools instead:

```bash
amo-cli graph-search --query "why did retry logic change"
amo-cli graph-status
```

By default these graph commands call the daemon, because the daemon is the Kuzu owner:

```powershell
python -m agent_memory_orchestrator.daemon --amo-home "$env:USERPROFILE\.agent-memory-orchestrator"
python -m agent_memory_orchestrator.cli graph-drain --limit 100 --max-windows 1
python -m agent_memory_orchestrator.cli graph-search --query "why did this change?"
```

Use `--offline` only for single-process maintenance when the daemon is stopped:

```bash
amo-cli graph-search --query "why did this change?" --offline
amo-cli graph-status --offline
```

Debug the new pipeline stage by stage:

```bash
amo-cli debug hooks
amo-cli debug drain --session-id SESSION_ID
amo-cli debug qwen --sample "what did we decide about codex hooks"
amo-cli debug graph --session-id SESSION_ID
amo-cli debug retrieval --query "why did this change?"
```

Drain in bounded Qwen windows so one request does not monopolize the daemon:

```bash
amo-cli graph-drain --limit 100 --max-windows 1
```

The default `drain_max_windows_per_run` is `3`; increase only when Qwen is warm and fast.

Finalize a session into central committed graph memory:

```bash
amo-cli graph-finalize-session --session-id SESSION_ID --commit HEAD
amo-cli graph-finalize-session --session-id SESSION_ID --commit HEAD --apply
```

The first command is a dry run. It shows which answer-grade draft nodes would promote and which version edges would be written. The apply command promotes only `Decision`, `WorkChange`, `Fix`, `Bug`, `Blocker`, `TestRun`, and selected `ContextSnapshot` nodes. Raw evidence, cleaned windows, graph deltas, sessions, repos, branches, files, and topics remain support/provenance nodes.

If an early drain promoted raw hook/install/test payloads into draft answer nodes, quarantine them without deleting evidence:

```bash
amo-cli graph-cleanup-noisy --limit 500
amo-cli graph-cleanup-noisy --limit 500 --apply
```

The first command is a dry run. The second marks noisy draft answer nodes as `abandoned`; raw evidence remains available through `amo_raw_evidence`.

Classify graph knowledge into duplicate/refinement/supersession/contradiction edges and topic clusters:

```bash
amo-cli graph-consolidate --limit 500
amo-cli graph-consolidate --limit 500 --apply
```

Rebuild the derived lexical retrieval cache after large drains or cleanup/consolidation runs:

```bash
amo-cli graph-cache-status
amo-cli graph-rebuild-cache --limit 5000
```

The cache is derived from answer-grade Kuzu nodes. Kuzu remains the source of truth; deleting the cache only makes retrieval slower until the next rebuild.

Rebuild the central Kuzu graph from raw evidence when the graph needs a clean replay:

```bash
amo-cli graph-rebuild-central --from-evidence --backup-current
amo-cli graph-rebuild-central --from-evidence --backup-current --apply
```

The dry run reports evidence roots, target rebuild path, and backup path. Apply drains raw evidence through the current cleaning/extraction gates, finalizes detected commit windows, consolidates clusters/version edges, smoke-checks the rebuilt graph, backs up the old graph, swaps the rebuilt graph into place, and rebuilds the cache.

Import recent Codex sessions as a local memory dataset:

```bash
amo-cli import-codex-sessions --root %USERPROFILE%\.codex\sessions --limit 5 --defer-vectors
amo-cli rebuild-indexes --force-vectors
```

Historical imports skip already-imported sessions by default to avoid duplicate memories. Use `--include-existing` only when intentionally replaying the same sessions into a fresh or throwaway DB.

Build a clean test DB from raw Codex sessions without polluting the default DB:

```bash
amo-cli rebuild-clean-db --out .data/clean-codex.db --codex-root %USERPROFILE%\.codex\sessions --limit 30 --force
```

`amo-cli install --target codex --dry-run` prints the exact Codex hook/MCP block before applying it.

Automatic memory injection into every Codex prompt is intentionally disabled. Retrieval should be explicit through MCP (`amo_graph_search`) or CLI (`amo-cli graph-search`).

### 5) Export memory snapshot

```bash
amo-cli export --out ./exports/memory_snapshot.jsonl
```

## MCP client wiring

Point Claude/Codex MCP configuration to run:

```bash
python -m agent_memory_orchestrator.mcp.server
```

Both agents then operate on the same local memory and orchestration state.

Phase 2 memory tools are implemented behind a testable service module:

```text
agent_memory_orchestrator.mcp.tools.MemoryMcpToolService
```

The legacy `agent_memory_orchestrator.mcp_server` and `agent_memory_orchestrator.mcp_memory_tools` modules remain as compatibility shims. The FastMCP server only registers tool functions and delegates to that service. New GraphRAG tools are `amo_graph_search`, `amo_current_context`, `amo_decision_history`, `amo_work_history`, `amo_raw_evidence`, and `amo_merge_status`.

## Adapter Layer

Provider/app payloads are normalized before storage through:

```text
agent_memory_orchestrator.integrations.adapters
```

Implemented adapters:

- `codex`: Codex rollout JSONL, session metadata, user/agent messages, command/tool results.
- `claude`: Claude hook/session/message payloads.
- `omnara`: optional visibility events marked `authoritative=false`.
- `base`: shared `NormalizedAdapterEvent` contract and generic fallback.

Redaction still happens centrally in `MemoryService.add_event` before persistence.

## Local model behavior

Memory operations remain offline. Kuzu is embedded and Qwen runs locally through Ollama. Optional embedding/reranker packages can be installed with:

```bash
pip install -e ".[models]"
```

Defaults target:

- Embeddings: `BAAI/bge-m3`
- Reranker: `BAAI/bge-reranker-base`
- Vector cache: FAISS when available

Legacy vector/reranker runtime tries locally available models only. New GraphRAG retrieval requires local Qwen via Ollama for query planning and context compression.

Model downloads are explicit setup actions. AMO will not silently download models during normal retrieval.

Qwen preset defaults:

- `cpu-light`: `qwen3:1.7b`
- `cpu-balanced`: `qwen3:1.7b`
- `gpu-quality`: `qwen3:8b`

If `qwen3:1.7b` cannot load on a very constrained machine, install with an explicit smaller override:

```bash
amo-cli install --target codex --preset cpu-light --qwen-model qwen3:0.6b --yes
```

List hardware-oriented presets:

```bash
amo-cli models list
```

Recommended presets:

- `cpu-light`: lower memory CPU setup, faster but less accurate.
- `cpu-balanced`: default local production setup, `BAAI/bge-m3` + `BAAI/bge-reranker-base`.
- `gpu-quality`: heavier reranker for GPU/high-RAM machines.

Check what is already cached locally:

```bash
amo-cli models status --preset cpu-balanced
```

Download/cache selected models once:

```bash
amo-cli models download --preset cpu-balanced
```

Verify production retrieval can load models from local disk:

```bash
amo-cli models preflight --preset cpu-balanced
```

Installer-selected models are persisted in `AMO_HOME/config.json`. If you change the embedding model later, rebuild vectors:

```bash
amo-cli rebuild-indexes --force-vectors
```

## Retrieval Quality Evals

Regression fixtures live under `tests/fixtures/`. They encode expected behavior such as:

- Codex hook queries should include `codex_hooks`, `UserPromptSubmit`, `PostToolUse`, and `Stop`.
- Context packs should prefer high-scoring durable memories instead of blindly sorting all decisions first.
- Packed context must not include IDE setup blocks, open tabs, raw `call_id` payloads, or MCP invocation JSON.
- Duplicate/superseded memory behavior should be inspectable through `consolidation_decisions` and KG edges.

Run the eval-backed tests with:

```bash
pytest tests/test_memory_service.py::test_codex_hooks_retrieval_eval_fixture -q
```

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
- Expand retrieval eval fixtures from real sessions before enabling broader auto-injection.
- Add Phase 2 Git-like Work Ledger for AI-produced code changes.
- Add app connectors and the private agent hub after the personal memory engine is stable.
- Add policy/rubric scoring for stronger auto-consensus.
- Add web UI for manual memory search and orchestrator approvals.
