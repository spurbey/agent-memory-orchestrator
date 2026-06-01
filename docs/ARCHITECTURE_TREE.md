# Codebase Architecture Tree

AMO is a local-first reasoning memory product. The source tree should tell an
engineer where product behavior lives, where adapters live, and which roots are
kept only for compatibility.

## Product Flow

```text
runtime hooks / connectors
-> evidence ledger
-> application pipeline
-> domain reasoning, code, versioning, and retrieval contracts
-> infrastructure stores and model providers
-> application query services
-> runtime MCP, daemon, CLI, and web surfaces
```

The central product invariant is:

```text
raw evidence is immutable
session graph is provenance
central graph is durable evolving memory
retrieval is an active graph view plus provenance trace
```

## Source Roots

### Product Domain

- `domain/`: pure models, contracts, and deterministic algorithms. This is
  where reasoning, code, versioning, retrieval, pipeline, peer, connector, and
  evidence concepts are defined without runtime ownership.
- `evidence/`: active append-only evidence ingestion, raw store, drain, trigger,
  and window utilities. This root remains active because hooks and drain use it
  directly.
- `versioning/`: active local work ledger and Git snapshot contracts.
- `peer/`: peer-agent and peer-network product code.

### Product Application

- `application/pipeline/`: durable production job runner, stage functions,
  stage artifacts, debug exports, and evaluations.
- `application/services/`: grouped product use-case services. Root files are
  compatibility exports only; implementation lives under capability packages
  such as `retrieval/`, `memory_graph/`, `central_merge/`, `session/`,
  `pipeline/`, `capture/`, `peer/`, `review/`, and `connectors/`.

### Infrastructure

- `infrastructure/`: concrete adapters for SQLite, Kuzu, FAISS, LLM/model
  providers, filesystem, Git, and related persistence/runtime dependencies.
- `llm/`: local model and reranker helpers that are still shared across
  application services. Keep model-provider ownership here until moved behind
  infrastructure ports.
- `integrations/`: external integration adapters that are not core domain.
- `install/`: installer and local setup orchestration.
- `bin/`: packaged native helper binaries and runtime assets.

### Runtime And Interfaces

- `runtime/`: CLI, daemon, MCP, hook launcher, and command entrypoints.
- `web/`: dashboard API/static assets for operating and debugging AMO.
- `skill_checkpoint/`: skill checkpoint production surface.
- `core/`: shared configuration and small common primitives.
- `orchestration/`: public orchestration facade retained for package API.
- `extensions/`: extension contracts and safe extension loading.

### Compatibility And Legacy-Public Roots

- `graph/`: compatibility exports for older graph import paths. Product
  implementation must live under `domain/`, `application/`, or
  `infrastructure/`.
- `memory/`: legacy-public memory service API. It is not the production
  reasoning-memory pipeline, but it remains covered until we explicitly remove
  that public API.
- `retrieval/`: legacy-public retrieval helpers used by the legacy memory API.
  Production graph retrieval lives under `domain/retrieval` and
  `application/services/retrieval`.

## Retired Roots

- `reasoning_graph/`: retired source root. The reasoning graph is still a
  product concept and documented under `docs/reasoning_graph/`, but production
  implementation now lives in the domain, application, infrastructure, and
  runtime hierarchy.

## Adding Features

- New capture source: start in `domain/evidence` or a source-specific domain
  package, add connector/runtime ingestion under `application/services` and
  concrete adapters under `infrastructure` or `integrations`.
- New production stage: define contracts in `domain/pipeline`, implement the
  stage in `application/pipeline/stages`, store artifacts through
  `application/pipeline`, and expose operator visibility through `runtime` and
  `web`.
- New retrieval behavior: put query intent, ranking, trace, and projection
  rules under `domain/retrieval`; use `application/services/retrieval` for the
  callable use case; keep SQLite/FAISS/Kuzu details in infrastructure.
- New graph mutation: plan and validate in domain/application services, mutate
  only through infrastructure graph stores, and record graph commits or audit
  artifacts before retrieval rebuilds.
- New peer or connector behavior: keep transport/protocol policy in domain,
  service orchestration in application, concrete network clients in
  infrastructure or `peer-netd`, and user-facing commands in runtime.

## Cleanup Rule

A file may stay only if it is one of these:

- Product implementation in the correct hierarchy.
- Public compatibility shim with tests proving it stays thin.
- Legacy-public API with tests proving current behavior.
- Documentation, fixture, or runtime asset intentionally referenced by the
  product.

Everything else should be removed, not shuffled.
