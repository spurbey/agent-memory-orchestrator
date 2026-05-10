# AMO Repository Layout

This repository is organized as a local-first Python package plus a small npm installer wrapper.

## Public Layout Target

```text
agent-memory-orchestrator/
  src/agent_memory_orchestrator/
    app/              # CLI, daemon routes, HTTP helpers
    config/           # settings, env, path resolution
    evidence/         # raw evidence, drain cursors, clean windows, triggers
    graph/            # Kuzu store, graph service, merge, cache, consolidation
    retrieval/        # lexical/vector/rerank/context-pack retrieval
    llm/              # local model clients and model management
    integrations/     # agent adapters and external connectors
    mcp/              # MCP server and tool facade
    install/          # hook/MCP installer logic
    orchestration/    # multi-agent review workflow
    versioning/       # Git/work-ledger integration
    privacy/          # redaction and cleaning policy
    web/              # packaged daemon web assets
  tests/              # mirrors package areas where practical
  docs/               # architecture, runbooks, development docs
  scripts/            # dev, smoke, release helpers
  npm/                # npx-style installer package
```

## Current Migration Policy

- Already extracted packages: `app/`, `evidence/`, `graph/`, `install/`, `llm/`, `mcp/`.
- Move one bounded subsystem per commit.
- Keep compatibility shims at old import paths until a minor release boundary.
- Run `ruff check src tests` and `python -m pytest -q` after each move.
- Do not move runtime state, caches, local evidence, or generated graph files into tracked paths.

## Import Compatibility

When moving a module, keep the old module as a thin shim:

```python
from .new_package.module import PublicClass, public_function
```

This lets existing user scripts and MCP/CLI imports keep working while the public source tree becomes understandable.
