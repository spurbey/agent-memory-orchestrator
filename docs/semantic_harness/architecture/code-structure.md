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
  query_modes/
    context_for_anchor.py       # mode orchestration only
    context_models.py           # context mode result models
    context_routes.py           # context question-type routes
    question_classifier.py      # question -> route types
    rank_tool_hits.py           # future rank-only search-result mode
    pre_edit.py                 # future planned-edit review mode
    relationship.py             # future multi-anchor relationship mode
    history.py                  # future version/work-history mode
    semantic_diff.py            # future patch/diff review mode

  planning/
    query_ir.py                 # backend-neutral query plan data
    planner.py                  # mode -> plan assembly
    scoring.py                  # reusable score composition
    suppression.py              # duplicate/budget/safety suppression

  traversal/
    weighted_paths.py           # weighted bounded graph expansion
    proximity.py                # PPR/RWR-style proximity helpers
    connectors.py               # Steiner-style connector candidates
    diversification.py          # MMR/non-duplicate path selection

src/agent_memory_orchestrator/application/services/semantic_harness/
  runtime/
    service.py                  # graph lifecycle, cache, persistence coordination
    mode_router.py              # mode dispatch and legacy compatibility routing
    compatibility.py            # future request/response compatibility adapters

  tool_context/
    extract.py                  # PostToolUse/tool-output parsing
    planner.py                  # shadow attach/suppress planning
    replay.py                   # eval replay only
    search_focus.py             # frozen probe logic until migrated

src/agent_memory_orchestrator/runtime/
  mcp/                          # transport functions only
  cli/commands/semantic_harness.py
                                # operational bootstrap/replay commands only

src/agent_memory_orchestrator/infrastructure/
  sqlite/semantic_harness/      # SQLite graph/projection stores
  helixdb/semantic_harness/     # future spike adapter only
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

New backend capability
-> infrastructure/<backend>/semantic_harness/
-> exposed through backend-neutral Query IR, not direct mode calls

New Codex/MCP/proxy behavior
-> runtime transport or integration docs
-> never in domain query algorithms
```

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
