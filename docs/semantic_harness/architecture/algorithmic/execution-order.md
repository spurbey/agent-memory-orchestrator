# Algorithmic Execution Order

## Purpose

Define the order for the next structural algorithm phases and the code hierarchy
they should use.

## Target Code Hierarchy

```text
domain/semantic_harness/query_modes/
  rank_tool_hits/
    parser.py
    models.py
    features.py
    scorer.py
    selector.py
    mode.py

  relationship/
    models.py
    seed.py
    edge_costs.py
    expand.py
    connectors.py
    path_ranker.py
    mode.py

  pre_edit/
    models.py
    grounding.py
    frontier.py
    risk.py
    tests.py
    mode.py

  history/
    models.py
    timeline.py
    source_quality.py
    mode.py

domain/semantic_harness/traversal/
  bounded.py
  proximity.py
  connectors.py
  diversity.py

domain/semantic_harness/scoring/
  features.py
  relation_strength.py
  source_quality.py
  risk.py

application/services/semantic_harness/evals/
  baselines.py
  fixtures.py
  rank_tool_hits.py
  relationship.py
  pre_edit.py
```

Small single-file mode modules are acceptable for the first slice. Split into
the hierarchy above when the mode crosses one responsibility boundary, not after
it becomes a monolith.

## Execution Order

Recommended next sequence:

```text
1. Build rank_tool_hits as rank-only mode over rg/grep output.
   Include candidate-local semantic similarity from UserPromptSubmit + goal
   against projection docs attached to raw tool-output candidates.
2. Build relationship_between_anchors v1 structural connector.
3. Build pre_edit_review v1 structural risk reviewer.
4. Add relation-weight scoring module with configurable gates.
5. Run fixture evals for each mode against no-AMO baseline.
6. Only then add semantic enrichment into relationship/history/pre-edit scoring.
7. Add Codex proxy delivery canary after rank_tool_hits proves value.
```

The certified non-derivable eval remains required for product proof, but it does
not block structural algorithm development as long as structural modes do not
claim semantic truth.

## Mode Slice Template

For each new mode, build in this order:

```text
1. Contract and result model
2. Input parser or normalizer
3. Anchor mapper
4. Feature extractor
5. Scorer or traversal policy
6. Selector or diversifier
7. Mode function
8. Runtime router dispatch
9. Focused unit tests
10. Small real-session eval artifact
```

Do not add a mode to MCP instructions until the local mode function and runtime
router are tested.

For `rank_tool_hits`, the first implementation must expose feature ablations:

```text
raw rg baseline
structural-only ranker
structural + candidate-local embedding ranker
```

The embedding path must use an actual model in at least one eval. The hash
fallback is useful for offline tests but is not enough to prove ranking quality.

## Proxy Delivery Sequence

Proxy work is delivery, not graph/query logic. It comes after ranker behavior is
testable locally.

```text
1. Build a forward-only Codex proxy canary.
2. Wrap Codex config with snapshot/unwrap support.
3. Prove HTTP /v1/responses passthrough.
4. Prove WebSocket /v1/responses passthrough when Codex uses WS.
5. Log tool-output items without mutation.
6. Mutate only rg/grep tool-output items into ranked-first/raw-preserved text.
7. Run Codex-with-proxy vs Codex-raw-rg eval.
```

The proxy must fail open. If ranking, graph access, embedding lookup, or raw-ref
storage fails, the original provider request is forwarded unchanged.

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
