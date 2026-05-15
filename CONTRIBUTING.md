# Contributing

## Development Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

On macOS/Linux:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

Run before opening a pull request:

```bash
python -m pytest -q
ruff check src tests
```

For packaging changes:

```bash
python -m build
python -m twine check dist/*
```

For npm installer changes:

```bash
cd npm/agent-memory-orchestrator-cli
npm run check
npm pack --dry-run
```

## Pull Requests

- Keep each pull request scoped to one behavior change, refactor, or documentation update.
- Include the validation commands you ran in the pull request template.
- Update public docs when a command, configuration key, graph schema, or MCP tool changes.
- Do not commit generated metadata, local evidence, local graph stores, SQLite databases, logs, or `.tmp` artifacts.
- Use security advisories for vulnerabilities; do not open public issues with exploit details or secrets.

## Architecture Boundaries

- Hooks are capture-only and must fail open.
- Kuzu is the primary graph truth.
- SQLite remains a compatibility/debug memory store.
- Retrieval is explicit through CLI/MCP, not automatic prompt injection.
- Daemon/API code belongs under `src/agent_memory_orchestrator/app/`.
- GraphRAG code belongs under `src/agent_memory_orchestrator/graph/`.
- Raw evidence and drain/window code belongs under `src/agent_memory_orchestrator/evidence/`.
- Local model clients belong under `src/agent_memory_orchestrator/llm/`.

## Release Hygiene

Do not commit runtime state, evidence, graph files, local databases, exported memory, logs, or connector secrets. Keep public docs focused on install, first successful run, and safe diagnostics.
