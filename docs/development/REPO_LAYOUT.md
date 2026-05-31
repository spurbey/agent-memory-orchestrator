# AMO Repository Layout

AMO is being migrated toward a layered, local-first architecture. The migration is staged so production imports, persisted stores, CLI/MCP wiring, and user installs keep working while the source tree becomes modular.

## Target Shape

```text
agent_memory_orchestrator/
  domain/
    evidence/
    reasoning/
    code/
    versioning/
    retrieval/
    peer/
    connectors/

  application/
    ports/
    services/
    workflows/

  infrastructure/
    sqlite/
    kuzu/
    faiss/
    git/
    llm/
    peer_netd/
    slack/
    filesystem/

  runtime/
    cli/
    daemon/
    mcp/
    hook/
    web/

  integrations/
    adapters/
    connectors/

  extensions/
    contracts/
    loader.py
    registry.py

  compatibility/
    old_imports.py
```

## Current Migration Policy

- Move one bounded subsystem per commit.
- Keep existing root wrappers and persisted database names until a deliberate release boundary.
- Prefer explicit compatibility exports over wildcard re-exports.
- Keep application services and workflows as thin coordination boundaries unless production behavior already exists.
- Do not change retrieval ranking, central merge semantics, evidence capture, or production pipeline behavior during structural refactors.
- Do not delete raw evidence handling or current production tests.
- Remove legacy `GraphDelta` / `ContextSnapshot` smoke coverage only when the covered path is no longer reachable from rebuild, compatibility, MCP, dashboard, or production fallback flows.
- Run `python -m ruff check src tests` and focused pytest suites after each structural move.

## Staged Migration

```text
Stage 1: production pipeline + retrieval boundaries
Stage 2: central merge/versioning boundaries
Stage 3: daemon/CLI/MCP runtime split
Stage 4: peer-agent + peer-netd split
Stage 5: connector ingestion/responding split
Stage 6: active-session local agent review/blast-radius workflows
Stage 7: plugin contracts and private extension loader
Stage 8: obsolete legacy tests/code removal
```

## Compatibility Rule

Old imports stay as thin shims until callers are migrated:

```python
from .new_package.module import PublicClass, public_function
```

New code should prefer the layered package owner:

```text
domain        = pure contracts and deterministic domain helpers
application   = service/workflow coordination and ports
infrastructure = SQLite, Kuzu, FAISS, Git, LLM, filesystem, network adapters
runtime       = CLI, daemon, MCP, hook entrypoints
integrations  = external agent/connector adapters
extensions    = local plugin contracts, registry, and loader
compatibility = documented old import surfaces
```

The `compatibility.old_imports` registry is the source of truth for known legacy import paths. Existing `app/`, root module wrappers, and other historical shims are compatibility surfaces, not the target architecture.
