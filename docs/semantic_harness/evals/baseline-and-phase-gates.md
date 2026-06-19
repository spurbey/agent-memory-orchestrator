# Baseline And Phase Gates

## Purpose

Every mode must prove value against not using AMO. Internal accuracy is not
enough.

## Baselines

Record for each fixture:

- no-AMO Codex session
- current AMO probe/card behavior when applicable
- new mode behavior

Classify every expected answer by derivability:

```text
shortcut fixture:
  answer is derivable from current code or docs
  AMO can win on fewer commands, files opened, tokens, or latency

memory fixture:
  answer requires git history, agent/session evidence, human intent, or runtime observation
  AMO must surface information the baseline cannot derive from current code alone
```

Shortcut fixtures are allowed as smoke tests. Product-value claims require
memory fixtures.

Metrics:

```text
wrong_edit_avoidance
semantic_invariant_hit_rate
time_to_right_file
files_opened_delta
irrelevant_files_opened_delta
test_selection_hit_rate
top3_file_hit_rate
hidden_file_recall_top5
semantic_reason_quality
strict_precision
mislead_rate
latency_p95
token_overhead_p95
stable_replay_rate
```

## Phase 0 Gate

- current vs target documented
- probe path frozen
- mode contract defined
- question classifier defined
- baseline fixtures selected

## Phase 1 Gate: Context For Anchor

- question-driven request required or strongly preferred
- question classifier fixture passes
- semantic-first output
- selective graph links only when answer-relevant
- baseline tests semantic misunderstanding prevention
- beats no-AMO baseline on wrong-edit avoidance or discovery cost
- honest partial states
- result distinguishes derivable shortcut facts from non-derivable memory facts
- product-value fixture includes at least one accepted non-derivable fact

The edge-count task is a valid plumbing fixture, not the decisive product-value
fixture, because the deduplication invariant can be derived from current schema
and graph-store code.

## Phase 2 Gate: Rank Tool Hits

- real rg replay passes
- beats raw-rg baseline
- rank-only output
- no Qwen dependency
- no generic cards
- score components visible in eval output
- top-ranked groups cite parsed file/line/symbol grounding
- latest captured UserPromptSubmit is used as a similarity query input when
  available
- candidate-local projection docs are searched only for files/symbols returned
  by the raw tool output
- actual embedding backend is exercised in at least one eval
- hash fallback is labeled degraded and cannot be the sole product proof
- ablation compares raw rg, structural-only ranker, and structural-plus-embedding
  ranker
- raw output is recoverable by raw_ref before any proxy mutation is considered

## Phase 3 Gate: Relationship Between Anchors V1

- resolves at least two anchors
- returns bounded structural paths only
- no unbounded graph walks
- edge kinds and path costs are visible in output
- co-change is labeled structural unless accepted reasons exist
- weak or missing semantic reasons produce `partial_structural`
- beats raw exploration on a structural relationship fixture

## Phase 4 Gate: Structural Pre-Edit Review

- planned edits map to graph nodes
- must-inspect tests/files surfaced
- beats no-AMO baseline
- no fake semantic risk
- decision is one of `go`, `edit_with_warnings`, or `blocked`
- risk findings expose feature components

## Phase 5 Gate: Relation Weights And Co-Change Scoring

- aggregate relation strength is separate from occurrences
- cochange_count and either_changed_count are consistent
- minimum occurrence gate is configurable, default 3
- score components are inspectable
- agent-facing output filters occurrences by task relevance
- structural fallback is labeled partial, not semantic causality

## Phase 6 Gate: Thin Semantic Enrichment

- source-aware packets for agent, human, PR, and imported commits
- manual reason-quality check on 5-10 known commits
- review rejects generic or unsupported reasons
- accepted facts include derivability labels
- at least one known fixture produces a useful non-derivable fact

## Phase 7 Gate: Relationship And History With Semantics

- known relationship fixture explained
- history cites versions, commits, and work windows
- semantic absence returns partial

## Phase 8 Gate: Semantic Diff

- actual patch hunks map to symbols or code regions
- planned edits vs actual edits are compared
- unplanned affected nodes are surfaced
- stale accepted facts are demoted or warned
- output cites diff hunks and graph anchors

## Phase 9 Gate: HelixDB Spike

- same planner runs over spike
- quality and latency compared to current store
- no forced adoption without measurable benefit

## Phase 10 Gate: Proxy Delivery

- MCP proven useful
- append-only proxy canary succeeds
- raw output recovery works
- mislead and token gates pass
- config wrap and unwrap preserve the original Codex config
- active Codex transport is identified as HTTP Responses or WebSocket Responses
- auth succeeds through the proxy
- streaming remains interactive
- proxy mutation is fail-open
- ranked-first rg/grep mutation beats raw rg baseline before ranked-only
  replacement is considered
