# AMO Code Architecture Tree

This document is the canonical map for the current AMO source tree. It explains
where product behavior belongs, how data moves through the system, and which
files are domain logic, application orchestration, infrastructure adapters,
runtime surfaces, or compatibility shims.

AMO is not general chat memory. The core product is local-first code-work
reasoning memory:

```text
Git tells what changed.
AMO tells why it changed, what evidence supported it, what code/commit it
touched, what tests validated it, and how that reasoning evolves over sessions.
```

## Non-Negotiable Product Invariants

```text
raw evidence is immutable
session graph is provenance
central graph is durable evolving memory
retrieval is active memory plus provenance trace
hooks capture only and fail open
daemon owns expensive graph/model/index work
hosted models are providers, not owners of state
```

The architecture must keep these layers separate:

```text
capture/runtime surfaces
-> evidence ledger
-> production pipeline
-> domain reasoning/code/versioning/retrieval contracts
-> graph and retrieval stores
-> query services
-> MCP/CLI/web/peer/connector interfaces
```

## End-To-End Data Flow

### Coding Session Capture

```text
runtime/hook/launcher.py
-> evidence/raw_store.py
-> evidence/drain.py
-> infrastructure/sqlite/production_job_store.py
-> application/pipeline/job_runner.py
```

Hooks write raw evidence only. They do not call Qwen, Kuzu, FAISS, graph
projection, or retrieval rebuilds. The daemon drains evidence and enqueues closed
sessions after session boundaries are observed.

### Production Session Pipeline

```text
application/pipeline/stages/evidence_packets.py
-> application/pipeline/stages/qwen_reasoning.py
-> application/pipeline/stages/reasoning_review.py
-> application/pipeline/stages/code_graph.py
-> application/pipeline/stages/session_graph_write.py
-> application/pipeline/stages/central_version_merge.py
-> application/pipeline/stages/retrieval_projection.py
-> application/services/retrieval/embedding.py
-> application/services/retrieval/vector.py
```

The runner is resumable and stores job/stage state in SQLite. If a local model
or embedding provider is unavailable, a stage pauses rather than fabricating
reasoning or production vectors.

### Retrieval Query Flow

```text
runtime/mcp/tools/graph.py or runtime/daemon/routes/retrieval.py
-> application/services/retrieval/query.py
-> application/services/memory_graph/service.py
-> domain/retrieval/intent.py, fusion.py, classification.py, answer.py
-> infrastructure/sqlite/retrieval_store.py
-> infrastructure/faiss/embedding_store.py
-> infrastructure/kuzu/central_graph.py
```

Retrieval is not graph truth. Retrieval is a projection over graph memory with
BM25/FTS, exact token matching, optional vector search, rerank/fusion, graph
neighborhood expansion, and answer trace construction.

### Central Memory Flow

```text
session graph write
-> domain/versioning/central_merge/planner.py
-> domain/versioning/central_merge/decision.py
-> application/services/central_merge/service.py
-> application/services/central_merge/apply.py
-> infrastructure/sqlite/central_merge_store.py
-> infrastructure/kuzu/central_graph.py
```

Session nodes stay immutable. Central memory adds canonical atoms, versions,
graph commits, graph views, and status changes. Retrieval should prefer active
central memory but still cite session packets, commits, evidence, and code.

### Peer-Agent Flow

```text
runtime/mcp/tools/peer.py or runtime/cli/commands/peer/
-> peer/agent/service.py
-> peer/service.py
-> peer/netd_client.py
-> peer-netd Go sidecar through infrastructure/peer_netd/
```

Python owns memory, policy, room state, context assembly, and retrieval. The Go
sidecar owns network transport only.

### Connector Flow

```text
integrations/connectors/<source>/
-> application/services/connectors/runtime.py
-> domain/connectors/
-> application/services/capture/evidence_ingest.py
-> evidence/raw_store.py
```

Different sources can produce different event shapes, but they should converge
through source-specific connector adapters into normalized evidence events.

## Source Root Ownership

### `domain/`

Pure product concepts, models, contracts, and deterministic algorithms. Domain
code should not own SQLite, Kuzu, FAISS, HTTP, daemon state, CLI output, or local
filesystem side effects except explicit pure path normalization helpers.

```text
domain/
  evidence/
  reasoning/
  code/
  versioning/
  retrieval/
  peer/
  connectors/
  pipeline/
```

#### `domain/evidence/`

Defines evidence concepts before runtime storage.

Key files:

- `models.py`: canonical evidence/window data structures.
- `events.py`: event type helpers.
- `boundaries.py`: session and evidence boundary rules.
- `triggers.py`: deterministic trigger detection.
- `views.py`: evidence view construction used by the production pipeline.

#### `domain/reasoning/`

Owns deterministic reasoning extraction contracts and review rules.

Key files:

- `models.py`: reasoning node and extraction models.
- `decision_extraction.py`: decision extraction helpers.
- `decision_packets.py`: packet-level decision structures.
- `decision_quality.py`: quality checks for answer-grade reasoning.
- `chunking.py`: reasoning/evidence chunking.
- `extraction.py`: extraction contracts.
- `extraction_window.py`: extraction window construction.
- `graph_validation.py`: graph validation rules.

#### `domain/code/`

Owns code-work facts independent of storage.

Key files and folders:

- `analysis.py`: high-level code analysis coordination.
- `models.py`: code node/symbol/version models.
- `ast/`: AST dispatch and language-specific extraction.
- `ast/code_nodes.py`: code node creation from parsed structures.
- `ast/dispatch.py`: language dispatch.
- `ast/generic.py`: fallback AST handling.
- `ast/python.py`: Python-specific parsing.
- `ast/models.py`: AST domain records.
- `diff/`: Git diff and patch parsing.
- `diff/git.py`: Git command-backed diff reads.
- `diff/parser.py`: hunk parsing.
- `hunks/`: hunk package boundary.
- `symbols/records.py`: symbol records.
- `versions/records.py`: code version records.
- `versions/resolver.py`: version resolution logic.

#### `domain/versioning/`

Owns repository identity, graph version concepts, and central merge semantics.

Key files and folders:

- `models.py`: versioning domain records.
- `repo_identity.py`: repo identity construction.
- `repo_resolution.py`: resolving local repo context.
- `identity.py`: canonical identity helpers.
- `graph_commits.py`: graph commit domain objects.
- `graph_views.py`: graph view objects.
- `merge_relations.py`: central relation/status semantics.
- `flow.py`: version flow helpers.
- `central_merge/planner.py`: central merge plan construction.
- `central_merge/decision.py`: deterministic decision/problem matching.

#### `domain/retrieval/`

Owns retrieval semantics that should be independent of SQLite/FAISS/Kuzu.

Key files:

- `models.py`: retrieval document/candidate models.
- `intent.py`: query intent classification contract.
- `classification.py`: deterministic query classification helpers.
- `constants.py`: retrieval constants.
- `projection.py`: graph-to-retrieval-document projection.
- `fusion.py`: candidate score fusion.
- `answer.py`: answer assembly.
- `answer_trace.py`: provenance trace construction.
- `answer_timeline.py`: timeline/history answer support.
- `answer_utils.py`: answer formatting utilities.
- `policy.py`: graph expansion/retrieval policy.
- `text.py`: tokenization, normalization, and FTS query helpers.

#### `domain/peer/`

Pure peer models and policy. Concrete room storage and networking live elsewhere.

Key files:

- `models.py`: peer and context models.
- `protocol.py`: peer protocol contracts.
- `rooms.py`: room rules.
- `policy.py`: sharing/trust policy.

#### `domain/connectors/`

Connector-neutral event and response concepts.

Key files:

- `models.py`: connector domain models.
- `events.py`: normalized connector events.
- `responses.py`: response contracts.

#### `domain/pipeline/`

Pipeline constants and contracts shared across stages.

Key files:

- `constants.py`: production stage names and related constants.

### `application/`

Use-case orchestration. Application code can coordinate domain logic and
infrastructure ports, but should avoid embedding low-level SQL/Kuzu/HTTP details.

```text
application/
  pipeline/
  services/
  workflows/
  ports/
```

#### `application/pipeline/`

Durable production job runner, stage execution, artifacts, debug fixtures, and
evaluation.

Key files and folders:

- `job_runner.py`: main resumable production pipeline runner.
- `promotion.py`: promotion/install quality path for generated graph artifacts.
- `graph_records.py`: graph record building helpers.
- `graph_writer.py`: session graph write helper.
- `packet_helpers.py`: packet utilities.
- `quality_gates.py`: production quality gates.
- `qwen_checkpoint.py`: Qwen checkpoint integration.
- `retrieval_projection_helpers.py`: retrieval projection helpers.
- `stages/evidence_packets.py`: evidence view and work-packet stage.
- `stages/qwen_reasoning.py`: Qwen reasoning extraction stage.
- `stages/reasoning_review.py`: reasoning review stage.
- `stages/code_graph.py`: hunks, AST, symbols, and code links.
- `stages/session_graph_write.py`: session graph write stage.
- `stages/central_version_merge.py`: central merge stage.
- `stages/retrieval_projection.py`: retrieval projection stage.
- `debug/fixtures.py`: fixture export/import helpers.
- `debug/backfill.py`: backfill debug helpers.
- `evaluation/`: production retrieval and semantic evaluation modules.

#### `application/services/`

Product use-case services. Root-level files here are compatibility exports or
thin facades. Implementation belongs in capability folders.

Key folders:

- `capture/`: evidence ingestion service.
- `central_merge/`: central merge planning/apply/status/artifact services.
- `connectors/`: connector runtime service.
- `memory_graph/`: GraphRAG service, central graph browsing, search, version flow.
- `peer/`: peer-agent application facade.
- `pipeline/`: production pipeline service facade.
- `retrieval/`: query, embedding, vector, runtime, and answer-trace services.
- `review/`: local agent review service.
- `session/`: session boundary, graph runtime, detail, embeddings, paths.

Key root facades:

- `graph_rag.py`: compatibility export for graph RAG service.
- `central_merge_apply.py`: compatibility export for central merge apply.
- `central_trace.py`: central trace facade.
- `connector_runtime.py`: connector runtime facade.
- `evidence_ingest.py`: evidence ingest facade.
- `local_agent_review.py`: local review facade.
- `peer_agent.py`: peer agent facade.
- `production_pipeline.py`: production pipeline facade.

#### `application/workflows/`

Higher-level workflows that compose services for agent-facing tasks.

Key files:

- `active_session_context.py`: active session context building.
- `blast_radius.py`: code blast-radius workflow.
- `closed_session_pipeline.py`: closed session pipeline workflow.
- `connector_ingestion.py`: connector ingestion workflow.
- `peer_context_request.py`: peer context request workflow.
- `pr_review.py`: PR review workflow.

#### `application/ports/`

Interfaces that application services expect infrastructure adapters to satisfy.

Key files:

- `graph_store.py`: graph storage port.
- `retrieval_store.py`: retrieval store port.
- `embedding_store.py`: embedding store port.
- `llm.py`: LLM/model provider port.
- `git.py`: Git backend port.
- `evidence_store.py`: evidence store port.
- `central_merge_store.py`: central merge persistence port.
- `connector_transport.py`: connector transport port.
- `peer_transport.py`: peer transport port.

### `infrastructure/`

Concrete adapters. This is where SQLite, Kuzu, FAISS, filesystem, Git, LLM
providers, Slack transport, and peer-netd adapter code belongs.

```text
infrastructure/
  sqlite/
  kuzu/
  faiss/
  filesystem/
  git/
  llm/
  peer_netd/
  slack/
```

Key folders and files:

- `sqlite/production_job_store.py`: production job/stage/event store facade.
- `sqlite/production_jobs/base.py`: base SQLite job store primitives.
- `sqlite/production_jobs/sessions.py`: session job lifecycle and reset marker storage.
- `sqlite/production_jobs/central_merge.py`: central merge plan/commit/lock tables.
- `sqlite/production_jobs/semantic_eval.py`: semantic eval persistence.
- `sqlite/retrieval_store.py`: retrieval documents, projections, FTS, and lookup store.
- `sqlite/central_merge_store.py`: central merge store adapter.
- `kuzu/central_graph.py`: central graph access.
- `kuzu/graph_store/`: Kuzu graph store package split into helpers, models, memory, and Kuzu adapter.
- `faiss/embedding_store.py`: FAISS vector cache.
- `filesystem/artifacts.py`: artifact filesystem helpers.
- `filesystem/backups.py`: backup helpers.
- `filesystem/raw_jsonl.py`: raw JSONL helpers.
- `git/backend.py`: Git backend adapter.
- `git/diff.py`: Git diff adapter.
- `llm/embeddings.py`: embedding provider adapter.
- `llm/qwen.py`: Qwen/Ollama provider adapter.
- `llm/rerankers.py`: reranker adapter.
- `llm/text_embedder.py`: text embedding helper.
- `llm/vector_cache.py`: vector cache adapter.
- `peer_netd/client.py`: infrastructure peer-netd client export.
- `peer_netd/runtime.py`: infrastructure peer-netd runtime export.
- `peer_netd/service.py`: peer-netd service helper export.
- `slack/`: Slack transport adapters.

### `runtime/`

External process interfaces and runtime entrypoints.

```text
runtime/
  cli/
  daemon/
  hook/
  mcp/
  web/
```

Key folders and files:

- `cli/main.py`: CLI entrypoint.
- `cli/commands/`: command groups for bootstrap, connectors, debug, graph, install, memory, models, orchestration, peer, retrieval, skill checkpoint.
- `daemon/client.py`: daemon client.
- `daemon/auto_drain.py`: daemon drain loop.
- `daemon/coordination.py`: daemon coordination.
- `daemon/dashboard.py`: dashboard app assembly.
- `daemon/graph_access.py`: graph access helper.
- `daemon/logging.py`: daemon logging.
- `daemon/owner_lock.py`: owner lock.
- `daemon/payloads.py`: daemon payload helpers.
- `daemon/routes/`: HTTP route groups for connectors, graph, health, hooks, jobs, memory, peer, retrieval.
- `hook/launcher.py`: hook launcher used by installed Codex/Claude hooks.
- `mcp/server.py`: MCP server entrypoint.
- `mcp/tools/`: MCP tool groups and result shaping.
- `web/assets.py`: web asset serving.

### `peer/`

Peer product implementation. This root is active because peer federation has
domain, application, and local sidecar concerns that are specific enough to keep
together, while still split internally.

Key files and folders:

- `models.py`: peer config and node records.
- `auth.py`: HMAC payload wrapping/unwrapping.
- `transport_auth.py`: transport auth policy helpers.
- `cards.py`: peer card sharing/importing.
- `invites.py`: invite construction and validation helpers.
- `context.py`: context pack helpers.
- `doctor.py`: peer diagnostics.
- `store.py`: local peer room/config store.
- `service.py`: room, invite, and message orchestration.
- `netd_client.py`: local peer-netd HTTP client.
- `netd_runtime.py`: managed peer-netd process lifecycle.
- `netd_binary.py`: peer-netd binary discovery, build, install, and capability checks.
- `netd_platform.py`: platform-specific binary/process helpers.
- `netd_transport.py`: netd envelope normalization, raw message building, and legacy HTTP posting.
- `netd_service.py`: OS service/watch/startup helpers for peer-netd.
- `agent/service.py`: peer-agent ask/watch/finalize orchestration.
- `agent/llm.py`: peer-agent LLM gateway.
- `agent/prompts.py`: peer-agent prompt templates.
- `agent/quality.py`: response quality evaluation.
- `agent/schemas.py`: room message schemas and redaction helpers.
- `agent/responses.py`: peer response parsing and ranking.
- `agent/selection.py`: peer selection policy.
- `agent/service_utils.py`: pure peer-agent helpers.
- `agent/state.py`: peer-agent room state store.

### `evidence/`

Active append-only evidence ingestion and drain root. This root remains active
because hooks and daemon drain use it directly.

Key files:

- `raw_store.py`: append-only raw evidence store.
- `drain.py`: evidence drain and closed-session enqueue logic.
- `triggers.py`: trigger helpers.
- `window.py`: evidence window helpers.

### `install/`

End-user local install/config orchestration.

Key files:

- `service.py`: installer plan/apply/uninstall/doctor orchestration.
- `templates.py`: generated runtime config, hook launcher, Codex/Claude templates.
- `constants.py`: installer constants.
- `targets.py`: Codex/Claude target expansion.
- `io.py`: installer file read/write/backup helpers.
- `detection.py`: installed-hook detection helpers.

### `integrations/`

External integration adapters and connector implementations.

Key folders:

- `adapters/`: Codex, Claude, Omnara, and base adapter contracts.
- `connectors/base.py`: connector base.
- `connectors/slack/`: Slack config, client, event, manifest, service, socket mode, and wizard helpers.

### `extensions/`

Local extension/plugin contracts. Use this for future private or local-only
algorithms where the public package should expose contracts but not require the
private implementation.

Key files:

- `loader.py`: safe extension loader.
- `registry.py`: extension registry.
- `contracts/connector.py`: connector extension contract.
- `contracts/graph_algorithm.py`: graph algorithm contract.
- `contracts/local_agent_skill.py`: local agent skill contract.
- `contracts/reranker.py`: reranker contract.
- `contracts/retrieval_algorithm.py`: retrieval algorithm contract.

### `core/`

Shared configuration and common primitives.

Key files:

- `config.py`: settings and environment-backed configuration.
- `db.py`: legacy/common SQLite helpers and schema support.
- `models.py`: shared models.
- `privacy.py`: privacy helpers.

### `memory/` and `retrieval/`

Legacy-public memory API roots. These are not the production reasoning-memory
pipeline, but they remain covered and isolated until deliberately removed.

Key memory files:

- `service.py`: legacy memory service API.
- `storage.py`: legacy memory storage.
- `retrieval.py`: legacy retrieval facade.
- `pipeline.py`: legacy memory processing.
- `ingest.py`: legacy ingest helpers.
- `hooks.py`: legacy hook helpers.
- `snapshots.py`: legacy snapshots.
- `legacy_retrieval/`: legacy context pack and scoring helpers.
- `processing/`: legacy cleaning/chunking/extraction helpers.

Key retrieval files:

- `context_pack.py`: legacy context pack.
- `scoring.py`: legacy scoring.

### `graph/`

Compatibility and service-facing graph imports. Product implementation should
continue migrating into `domain/`, `application/`, and `infrastructure`, but this
root remains while older imports and tests depend on it.

Key files:

- `service.py`: compatibility graph service facade.
- `store.py`: compatibility graph store facade.
- `retrieval_policy.py`: compatibility retrieval policy helpers.
- `answer_context.py`, `answer_trace.py`, `central_trace.py`: answer/trace compatibility helpers.
- `constants.py`, `diagnostics.py`: compatibility constants and diagnostics.
- `version_flow.py`, `text_utils.py`: compatibility helpers.

### `llm/`

Compatibility exports for model-provider helpers. New provider work should use
`infrastructure/llm`.

Key files:

- `embeddings.py`, `qwen.py`, `rerankers.py`, `text_embedder.py`, `vector_cache.py`, `models.py`.

### `skill_checkpoint/`

Production surface for converting completed work into reusable skills.

Key files:

- `pipeline.py`: skill checkpoint generation/validation pipeline.

### `versioning/`

Local work ledger and Git facade retained as a public product surface.

Key files:

- `git.py`: Git work helpers.
- `ledger.py`: local work ledger.
- `models.py`: versioning models.
- `base.py`: base versioning helpers.

### `orchestration/`

Public orchestration facade retained for package API compatibility.

Key files:

- `service.py`: orchestration service facade.

### `web/`

Static dashboard assets. Daemon route code lives under `runtime/daemon/routes`.

Key folders:

- `css/`: styles.
- `js/control-room/`: control-room modules.
- `js/core/`: shared frontend utilities.
- `js/graph/`: graph explorer modules.

## Product Pipelines By Concern

### Evidence And Capture

```text
runtime hook or connector
-> application/services/capture/evidence_ingest.py
-> evidence/raw_store.py
-> evidence/drain.py
-> infrastructure/sqlite/production_job_store.py
```

For new sources such as browser capture or meetings, create source-specific
connector adapters and map their events into normalized evidence records before
they enter the drain/pipeline path.

### Reasoning And Code Graph

```text
domain/evidence/views.py
-> domain/reasoning/*
-> application/pipeline/stages/qwen_reasoning.py
-> application/pipeline/stages/reasoning_review.py
-> domain/code/*
-> application/pipeline/stages/code_graph.py
```

Qwen should explain packet-backed work. Deterministic code facts come from Git,
diffs, hunks, AST, symbols, and versions.

### Central Versioning

```text
domain/versioning/*
-> domain/versioning/central_merge/*
-> application/services/central_merge/*
-> infrastructure/sqlite/production_jobs/central_merge.py
-> infrastructure/kuzu/central_graph.py
```

Central merge should create durable atoms/versions and graph commits without
deleting session provenance.

### Retrieval And RAG

```text
domain/retrieval/projection.py
-> infrastructure/sqlite/retrieval_store.py
-> infrastructure/faiss/embedding_store.py
-> application/services/retrieval/query.py
-> domain/retrieval/answer_trace.py
-> runtime/mcp/tools/graph_results.py
```

Retrieval docs are projections. The graph remains truth. Vectors are candidates,
not proof.

### Peer-To-Peer Agent Context

```text
runtime/mcp/tools/peer.py
-> peer/agent/service.py
-> peer/service.py
-> peer/netd_transport.py
-> peer/netd_client.py
-> peer-netd sidecar
```

Peer-agent messages carry query, retrieval bundle, support, citations,
confidence, answer grade, and room state. Local policy decides what can be
shared.

## Where To Debug A Feature

### Hooks not capturing

Start at:

```text
runtime/hook/launcher.py
evidence/raw_store.py
runtime/daemon/routes/hooks.py
evidence/drain.py
```

Check hook config, raw JSONL writes, daemon route acceptance, and drain cursor.

### Closed session not processing

Start at:

```text
evidence/drain.py
infrastructure/sqlite/production_job_store.py
application/pipeline/job_runner.py
application/pipeline/stages/
```

Check session boundary detection, job row, current stage, artifact path, and
stage diagnostics.

### Retrieval answer is weak

Start at:

```text
application/services/retrieval/query.py
application/services/memory_graph/service.py
domain/retrieval/
infrastructure/sqlite/retrieval_store.py
infrastructure/faiss/embedding_store.py
infrastructure/kuzu/central_graph.py
```

Check active projection, doc counts, vector status, candidate fusion, graph
expansion, and answer trace.

### Central merge did not apply

Start at:

```text
application/pipeline/stages/central_version_merge.py
application/services/central_merge/
domain/versioning/central_merge/
infrastructure/sqlite/production_jobs/central_merge.py
infrastructure/kuzu/central_graph.py
```

Check merge plan status, review candidates, graph commit row, Kuzu writes, and
active graph view.

### Peer-agent context request failed

Start at:

```text
peer/agent/service.py
peer/agent/selection.py
peer/agent/responses.py
peer/service.py
peer/netd_transport.py
peer/transport_auth.py
peer/netd_runtime.py
peer/netd_client.py
```

Check peer selection, room state, auth policy, sidecar health, envelope
normalization, and response quality.

### Connector source not ingesting

Start at:

```text
integrations/connectors/<source>/
domain/connectors/
application/services/connectors/runtime.py
application/services/capture/evidence_ingest.py
evidence/raw_store.py
```

Check connector transport, event normalization, permissions, evidence write, and
daemon drain.

## Adding New Features

### New capture source

Use this path:

```text
domain/connectors or source-specific domain models
-> integrations/connectors/<source>
-> application/services/connectors/runtime.py
-> application/services/capture/evidence_ingest.py
-> evidence/raw_store.py
```

Do not bypass normalized evidence ingestion.

### New production pipeline stage

Use this path:

```text
domain/pipeline/constants.py
-> application/pipeline/stages/<stage>.py
-> application/pipeline/job_runner.py
-> infrastructure/sqlite/production_job_store.py
-> runtime/daemon/routes/jobs.py
-> web dashboard visibility
```

Each stage needs artifacts, diagnostics, retry behavior, and focused tests.

### New retrieval algorithm

Use this path:

```text
domain/retrieval/<algorithm>.py
-> application/services/retrieval/query.py
-> infrastructure adapter only if persistence/provider specific
-> runtime/mcp/tools/graph_results.py for result shape
```

Do not put ranking logic in daemon routes or MCP tools.

### New graph mutation

Use this path:

```text
domain/versioning or domain/reasoning
-> application/services/<use-case>
-> infrastructure/kuzu or infrastructure/sqlite
-> graph commit/audit artifact
-> retrieval projection rebuild
```

No graph mutation should happen without an inspectable plan/result or audit
artifact.

### New peer transport

Use this path:

```text
domain/peer for protocol concepts
-> peer/<transport helper> or infrastructure/<transport>
-> peer/service.py orchestration
-> peer/agent/service.py if agent context behavior changes
```

Do not move local memory or sharing policy into the Go sidecar.

### Private/local algorithms

Use this path:

```text
extensions/contracts/
-> extensions/loader.py
-> extensions/registry.py
-> local gitignored implementation package
```

Public code should expose the contract and safe loading boundary. Private code
can remain outside Git if needed.

## Refactor Rules

- Keep one bounded subsystem per commit.
- Preserve public import paths with thin compatibility facades when tests depend
  on them.
- Do not change retrieval ranking, central merge semantics, evidence capture, or
  pipeline stage behavior during structural moves.
- Do not physically collapse session graph nodes into central graph nodes.
- Do not delete raw evidence or reset stores during refactors.
- Add or keep focused tests around moved behavior.
- Run `python -m ruff check src tests`.
- Run focused pytest suites for the touched subsystem.
- Run `python -m pytest -q` before committing broad architecture changes.

## Retired Or Compatibility Roots

`reasoning_graph/` is retired as an implementation root. The reasoning graph is
still a product concept and remains documented under `docs/reasoning_graph/`.
Production implementation now belongs to `domain/`, `application/`,
`infrastructure/`, `runtime/`, and active product roots such as `evidence/` and
`peer/`.

`graph/`, `memory/`, `retrieval/`, `llm/`, `orchestration/`, and some root-level
application service files are retained for compatibility or legacy-public API
coverage. New product implementation should not grow those roots unless the file
is explicitly a facade.

`src/agent_memory_orchestrator/bin/` is optional generated/package output for
native helper binaries. It can exist in local release builds, but it is not a
required tracked source root.
