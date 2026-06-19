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

Required architecture:

```text
parse tool hits
-> map to graph anchors
-> collect candidate-local projection docs
-> compare UserPromptSubmit + goal + search terms to candidate docs
-> score with explicit components
-> diversify
-> return rank-only result
```

This mode must not reuse generic card output as its product shape.
Actual embeddings must be tested for at least one broad-search fixture. The hash
fallback is a degraded offline path, not proof of semantic ranking quality.

### 3. Relationship Between Anchors V1

Build `relationship_between_anchors` as a structural connector before relying
on semantic relation reasons.

Required architecture:

```text
resolve anchors
-> assign node prizes
-> assign edge costs
-> bounded expansion
-> compact connector candidates
-> path ranking
-> missing/weak link diagnostics
```

V1 uses structural edges and co-change gates only. It returns
`partial_structural` when reviewed relation reasons are missing.

### 4. Structural Pre-Edit Review

Build `pre_edit_review` v1 using structural impact, tests, docs, config,
co-change, and risk signals. It must not claim semantic risk without accepted
semantic evidence.

Required architecture:

```text
ground planned edits
-> build impact frontier
-> score structural risk
-> surface must-inspect files and tests
-> return go/edit_with_warnings/blocked
```

### 5. Relation Weights And Co-Change Scoring

Move relation scoring into a reusable scoring layer. Preserve occurrence-level
evidence separately from aggregate edge strength.

Required gates:

```text
cochange_count <= either_changed_count
stored_strength exposes score components
historical relation output requires configurable strength threshold
historical relation output requires configurable minimum occurrence count,
default 3
task-relevant occurrences are filtered before agent-facing output
```

### 6. Thin Semantic Enrichment

Add source-aware Qwen/provider/forked-agent enrichment for selected repo change
events. Provider or agent output must pass deterministic review and a manual
reason-quality gate before advanced algorithms depend on it.

This phase includes the certified non-derivable product-proof eval, but
structural modes may be built before it as long as they do not claim semantic
truth.

### 7. History And Semantic Relationship Algorithms

Build `relationship_between_anchors` and `history_for_anchor` after enough
semantic data exists to prove value. Structural-only outputs must stay honest
partials.

### 8. Semantic Diff

Build `semantic_diff` after `pre_edit_review` and commit-update mapping are
stable. It reviews actual hunks against planned edits and accepted semantic
constraints.

### 9. HelixDB Spike

Evaluate HelixDB behind the backend-neutral Query IR. Do not rewrite storage
until quality and latency beat the current backend on real fixtures.

### 10. Proxy Delivery

Use MCP as the proving surface first. Add proxy append-only delivery only after
mode outputs pass precision, mislead, latency, token, and raw-output recovery
gates.

First proxy target:

```text
Codex provider request
-> tool-output item containing rg/grep output
-> rank_tool_hits
-> ranked-first/raw-preserved mutation
-> upstream provider
```

The proxy must first prove config wrap/unwrap, auth forwarding, HTTP/WS
Responses passthrough, raw_ref storage, and fail-open behavior.

## Release Gates

Each phase must include docs, fixtures, no-AMO baseline comparison, compatibility
checks, evals, and a rollback path before the next phase starts.
