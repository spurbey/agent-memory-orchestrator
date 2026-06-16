# Implementation Roadmap

## Principle

Build structural usefulness before semantic richness. If the harness cannot help an agent navigate a repo from structure alone, adding Qwen and history will not fix the product.

## Sequence

### 0. Architecture Reset And Baseline

Document the current-vs-target boundary, freeze the probe card path, define the
mode-based contract, and record no-AMO/current-AMO baselines before feature
work. This phase owns the eval fixtures and phase gates.

### 1. Question-Driven Context For Anchor

Build `context_for_anchor` as a question-driven, semantic-first mode. It should
answer the agent's specific question about a known anchor and include only
action-relevant graph links.

### 2. Rank Tool Hits

Build `rank_tool_hits` for broad search output. It ranks file, line, symbol, and
region groups without returning verbose cards. This becomes critical for future
proxy interception.

### 3. Thin Semantic Enrichment

Add source-aware Qwen/provider enrichment for selected repo change events.
Provider output must pass deterministic review and a manual reason-quality gate
before advanced algorithms depend on it.

### 4. Structural Pre-Edit Review

Build `pre_edit_review` v1 using structural impact, tests, docs, config,
co-change, and risk signals. It must not claim semantic risk without accepted
semantic evidence.

### 5. Relationship And History Algorithms

Build `relationship_between_anchors` and `history_for_anchor` after enough
semantic data exists to prove value. Structural-only outputs must stay honest
partials.

### 6. HelixDB Spike

Evaluate HelixDB behind the backend-neutral Query IR. Do not rewrite storage
until quality and latency beat the current backend on real fixtures.

### 7. Proxy Delivery

Use MCP as the proving surface first. Add proxy append-only delivery only after
mode outputs pass precision, mislead, latency, token, and raw-output recovery
gates.

## Release Gates

Each phase must include docs, fixtures, no-AMO baseline comparison, compatibility
checks, evals, and a rollback path before the next phase starts.
