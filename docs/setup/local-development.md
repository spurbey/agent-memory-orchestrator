# Local Development

## Create an Environment

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,models]"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Initialize Local Stores

```bash
amo-cli init-db
amo-cli init-graph
```

## Run the Daemon

```bash
amo-daemon
```

Installed Codex/Claude setup can point the daemon at the user AMO home:

```powershell
amo-daemon --amo-home "$env:USERPROFILE\.agent-memory-orchestrator"
```

Open:

```text
http://127.0.0.1:8765
http://127.0.0.1:8765/graph
```

## Run MCP Server

```bash
amo-mcp
```

MCP clients should run:

```bash
amo-mcp
```

## Import Existing Sessions

```bash
amo-cli import-codex-sessions --root %USERPROFILE%\.codex\sessions --limit 5 --defer-vectors
amo-cli rebuild-indexes --force-vectors
```

Build a clean test DB from raw Codex sessions:

```bash
amo-cli rebuild-clean-db --out .data/clean-codex.db --codex-root %USERPROFILE%\.codex\sessions --limit 30 --force
```

## Debug the Pipeline

```bash
amo-cli debug hooks
amo-cli debug drain --session-id SESSION_ID
amo-cli debug qwen --sample "what did we decide about codex hooks"
amo-cli debug graph --session-id SESSION_ID
amo-cli debug retrieval --query "why did this change?"
```

## Export and Import

```bash
amo-cli export --out ./exports/memory_snapshot.jsonl
amo-cli import --file ./exports/memory_snapshot.jsonl
```

## Validate Changes

```bash
python -m pytest -q
python -m ruff check src tests
```
