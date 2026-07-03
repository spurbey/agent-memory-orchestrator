# Code Structure

## Purpose

Define where Semantic Harness code belongs before more runtime work is added.
This doc is a guardrail against monoliths and mid-implementation placement
decisions.

## Layer Rule

```text
domain = pure graph/query algorithms and data models
application = runtime orchestration, cache/persistence coordination, eval services
runtime = CLI/MCP/hook transport only
infrastructure = SQLite, HelixDB, vector, filesystem, and provider adapters
docs = product contracts, algorithm specs, eval gates
```

No layer should reach upward. Domain code must not open databases, read the
filesystem, call MCP, or know about Codex hooks.

## Target Tree

```text
src/agent_memory_orchestrator/domain/semantic_harness/
  query_plan.py                 # GraphSeed/EdgeExpansion/GraphSlicePlan contracts
  query_modes/
    context_for_anchor.py       # mode orchestration only
    context_models.py           # context mode result models
    context_routes.py           # context question-type routes
    question_classifier.py      # question -> route types
    rank_tool_hits.py           # initial rank-only search-result mode
    pre_edit.py                 # initial planned-edit review mode
    relationship.py             # initial multi-anchor relationship mode
    history.py                  # initial version/work-history mode
    semantic_diff.py            # initial patch/diff review mode

    rank_tool_hits/             # split here once parser/scorer/selector diverge
      parser.py
      models.py
      features.py
      scorer.py
      selector.py
      mode.py

    relationship/               # split here once connectors/path ranking start
      models.py
      seed.py
      edge_costs.py
      expand.py
      connectors.py
      path_ranker.py
      mode.py

    pre_edit/                   # split here once frontier/risk/test logic grows
      models.py
      grounding.py
      frontier.py
      risk.py
      tests.py
      mode.py

  semantic_facts/
    models.py                   # canonical source/derivability/scope/trust contracts
    review.py                   # deterministic proposal review
    attach.py                   # accepted facts -> graph node metadata
    extraction.py               # future provider/deterministic proposal builders

  scoring/
    features.py                 # reusable feature values, no product formatting
    relation_strength.py        # co-change and relation aggregate scoring
    source_quality.py           # trust/derivability/source scoring helpers
    risk.py                     # reusable structural risk scores

  planning/
    query_ir.py                 # backend-neutral query plan data
    planner.py                  # mode -> plan assembly
    scoring.py                  # reusable score composition
    suppression.py              # duplicate/budget/safety suppression

  traversal/
    bounded.py                  # typed bounded graph expansion
    weighted_paths.py           # weighted bounded graph expansion
    proximity.py                # PPR/RWR-style proximity helpers
    connectors.py               # Steiner-style connector candidates
    diversification.py          # MMR/non-duplicate path selection

src/agent_memory_orchestrator/application/services/semantic_harness/
  runtime/
    service.py                  # graph lifecycle, cache, persistence coordination
    mode_router.py              # mode dispatch and legacy compatibility routing
    query_planner.py            # request mode -> bounded graph slice plan
    ports.py                    # graph persistence and evidence-query boundaries
    compatibility.py            # future request/response compatibility adapters

  tool_context/
    extract.py                  # PostToolUse/tool-output parsing
    planner.py                  # shadow attach/suppress planning
    replay.py                   # eval replay only
    search_focus.py             # frozen probe logic until migrated

  evals/
    baselines.py                # no-AMO/current-AMO/new-mode comparisons
    fixtures.py                 # fixture loading and replay helpers
    rank_tool_hits.py
    relationship.py
    pre_edit.py

src/agent_memory_orchestrator/runtime/
  mcp/                          # transport functions only
  cli/commands/semantic_harness.py
                                # operational bootstrap/replay commands only
  cli/commands/semantic_harness_setup.py
                                # one-command local Helix setup orchestration
  codex_proxy/
    wrapper.py                   # config snapshot/inject/unwrap only
    server.py                    # HTTP/WS proxy transport only
    responses_http.py            # /v1/responses forwarding/mutation
    responses_ws.py              # WebSocket forwarding/mutation
    tool_outputs.py              # request item detection and raw_ref storage
    mutation.py                  # ranked-first/raw-preserved text rendering

src/agent_memory_orchestrator/infrastructure/
  sqlite/semantic_harness/      # legacy migration adapter and adapter tests
  embeddings/semantic_harness/   # embedding backend adapters and manifests
  helixdb/local_runtime.py      # CLI download, Docker health, local lifecycle
  helixdb/semantic_harness/
    codec.py                    # shared node/edge serialization
    graph_store.py              # complete-store writes and mutation operations
    evidence_query.py           # native bounded Helix traversal executor
    repository.py               # persistence and evidence-query adapter
    migration.py                # verified legacy graph cutover
```

## File Ownership Rules

- Mode files own one product mode only.
- Model files contain dataclasses/contracts only.
- Route files contain deterministic per-question/per-edge logic only.
- Runtime services coordinate stores and caches but do not rank, traverse, or
  classify.
- MCP files validate transport fields and call the runtime; they must not build
  graph plans.
- Infrastructure files execute storage/index operations; they must not decide
  product behavior.
- Eval files may assemble fixtures, but product logic must stay outside tests.

## Size And Split Rules

Use these thresholds as review triggers, not hard limits:

```text
mode orchestrator > 180 lines -> split routes or result shaping
route module > 250 lines -> split by question family
runtime service > 220 lines -> move compatibility/router/cache concern out
MCP tool file grows product logic -> reject and move logic to application/domain
```

## Legacy Path Policy

The old generic card path remains in:

```text
domain/semantic_harness/query.py
application/services/semantic_harness/tool_context/search_focus.py
```

Policy:

- bug fixes only
- no new product features
- no semantic-heavy ranking work
- migrate useful behavior into mode-specific modules after eval proof

## New Feature Placement

Use this decision table:

```text
New question type for context_for_anchor
-> query_modes/question_classifier.py
-> query_modes/context_routes.py
-> focused tests under tests/domain/semantic_harness/

New query mode
-> query_modes/<mode>.py
-> runtime/mode_router.py only for dispatch
-> MCP transport only after domain/runtime tests pass

New graph traversal algorithm
-> traversal/<algorithm>.py
-> used by mode module through a narrow function

New scoring feature
-> scoring/<feature_family>.py
-> used by one or more mode scorers
-> must expose inputs and components in eval output

New semantic fact source or review rule
-> semantic_facts/models.py for contract changes
-> semantic_facts/review.py for deterministic acceptance rules
-> semantic_facts/attach.py only if graph-write shape changes
-> provider-specific extraction stays outside domain

New backend capability
-> infrastructure/<backend>/semantic_harness/
-> exposed through backend-neutral Query IR, not direct mode calls

New Codex/MCP/proxy behavior
-> runtime transport or integration docs
-> never in domain query algorithms

New embedding backend
-> infrastructure/embeddings/semantic_harness/
-> exposed through projection/retrieval ports
-> domain rankers consume similarity features, not provider clients
```

## Algorithmic Mode Split Rule

For each new mode, start with this internal shape even if the first
implementation is one file:

```text
input parser
anchor mapper
feature extractor
scorer/traverser
selector/diversifier
result model
result renderer
eval adapter
```

Do not mix these concerns with MCP transport, SQLite persistence, provider
calls, or generic cards.

## Retire Or Migrate Rule

Probe logic should be retired only after a mode-specific replacement beats it on
the relevant baseline:

```text
search_focus -> rank_tool_hits
generic risk cards -> pre_edit_review
historical_relation cards -> relationship/history modes
why_changed cards -> history_for_anchor
tool overlay attach/suppress -> proxy append eval after rank_tool_hits
```

Until then, probe logic remains compatibility-only and receives bug fixes only.

## Review Checklist

Before committing Semantic Harness code:

```text
does this file own one responsibility?
did transport stay transport-only?
did runtime avoid mode-specific algorithms?
did domain avoid filesystem/database/provider calls?
did the legacy card path stay frozen?
are focused tests close to the changed layer?
```
