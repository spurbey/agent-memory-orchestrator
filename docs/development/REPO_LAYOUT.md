# AMO Repository Layout And Refactor Policy

This is the contributor-facing layout policy. For the detailed source tree and
per-file ownership map, read [AMO Code Architecture Tree](../ARCHITECTURE_TREE.md).

## Current Layering

```text
agent_memory_orchestrator/
  domain/          pure product models and deterministic algorithms
  application/     pipeline stages, services, workflows, and ports
  infrastructure/  SQLite, Kuzu, FAISS, Git, filesystem, LLM, network adapters
  runtime/         CLI, daemon, MCP, hooks, web entrypoints
  evidence/        active raw evidence store, drain, triggers, windows
  peer/            peer rooms, peer-agent, peer-netd lifecycle and transport
  integrations/    external connector and agent adapters
  install/         local installer/config/hook setup
  extensions/      extension contracts and local/private loader boundary
  core/            shared config and common primitives
```

Compatibility or legacy-public roots:

```text
graph/
memory/
retrieval/
llm/
orchestration/
skill_checkpoint/
versioning/
web/
```

Compatibility roots can stay while tests prove they are needed, but new
implementation should move toward the layered owner unless the file is a thin
facade.

Optional generated/package roots:

```text
bin/
```

`src/agent_memory_orchestrator/bin/` is not a required source root. Release
builds may generate packaged native helper binaries there, but CI and source
ownership tests must not require that directory to exist when no binary asset is
tracked.

## Product Boundary Map

```text
capture/evidence:
  runtime hooks/connectors -> evidence/raw_store.py -> evidence/drain.py

production pipeline:
  application/pipeline/job_runner.py -> application/pipeline/stages/*

reasoning/code facts:
  domain/evidence -> domain/reasoning -> domain/code

central memory:
  domain/versioning -> application/services/central_merge -> infrastructure/kuzu/sqlite

retrieval/RAG:
  domain/retrieval -> application/services/retrieval -> infrastructure/sqlite/faiss/kuzu

peer context:
  peer/agent -> peer/service -> peer/netd_transport -> peer-netd sidecar

connectors:
  integrations/connectors -> application/services/connectors -> evidence ingest

runtime surfaces:
  runtime/cli, runtime/daemon, runtime/mcp, runtime/hook, runtime/web
```

## Refactor Policy

- Move one bounded subsystem per commit.
- Keep persisted database names and schema behavior unless the task is an
  explicit migration.
- Preserve public imports with thin facades when tests or users depend on them.
- Do not reintroduce old `reasoning_graph/` implementation dependencies.
- Do not change retrieval ranking, central merge semantics, evidence capture, or
  pipeline behavior during structural-only refactors.
- Do not delete raw evidence handling or production tests.
- Do not shuffle code only to reduce line counts. Split only when the new file
  has a clear product responsibility.
- Prefer domain/application/infrastructure/runtime ownership over generic
  utility dumps.

## Test Policy For Structural Work

Always run:

```bash
python -m ruff check src tests
```

Then run focused tests for the touched subsystem. Examples:

```bash
python -m pytest tests/test_peer_rooms.py tests/test_peer_agent.py -q
python -m pytest tests/test_install_service.py tests/test_runtime_boundary_groups.py -q
python -m pytest tests/test_central_merge_decision.py tests/test_central_merge_planner.py -q
python -m pytest tests/infrastructure/faiss tests/test_retrieval*.py -q
```

Before committing broad architecture changes, run:

```bash
python -m pytest -q
```

## Where To Add New Code

Use the owner that matches the behavior:

| Behavior | Put domain rules in | Put orchestration in | Put adapters in | Put user surface in |
| --- | --- | --- | --- | --- |
| Evidence capture | `domain/evidence` | `application/services/capture` | `evidence`, `integrations` | `runtime/hook`, daemon routes |
| Production stage | `domain/pipeline` plus relevant domain | `application/pipeline/stages` | `infrastructure/*` | CLI/daemon/web |
| Retrieval ranking | `domain/retrieval` | `application/services/retrieval` | SQLite/FAISS/Kuzu adapters | MCP/daemon/web |
| Central merge | `domain/versioning` | `application/services/central_merge` | SQLite/Kuzu adapters | CLI/daemon/web |
| Peer-agent | `domain/peer` | `peer/agent`, `application/services/peer` | `peer/netd_*`, `infrastructure/peer_netd` | CLI/MCP |
| Connector | `domain/connectors` | `application/services/connectors` | `integrations/connectors` | CLI/daemon/web |
| Private algorithm | `extensions/contracts` | extension loader/registry | gitignored local implementation | configured extension |

## Cleanup Rule

A file should remain only if it is one of these:

- Current product implementation in the right hierarchy.
- Public compatibility facade with tests.
- Legacy-public API with tests.
- Documentation, fixture, packaged binary, or runtime asset referenced by the
  product.

Everything else should be removed deliberately, not hidden in another package.
