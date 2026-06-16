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

## Phase 3 Gate: Thin Semantic Enrichment

- source-aware packets for agent, human, PR, and imported commits
- manual reason-quality check on 5-10 known commits
- review rejects generic or unsupported reasons
- accepted facts include derivability labels
- at least one known fixture produces a useful non-derivable fact

## Phase 4 Gate: Structural Pre-Edit Review

- planned edits map to graph nodes
- must-inspect tests/files surfaced
- beats no-AMO baseline
- no fake semantic risk

## Phase 5 Gate: Relationship And History

- known relationship fixture explained
- history cites versions, commits, and work windows
- semantic absence returns partial

## Phase 6 Gate: HelixDB Spike

- same planner runs over spike
- quality and latency compared to current store
- no forced adoption without measurable benefit

## Phase 7 Gate: Proxy Delivery

- MCP proven useful
- append-only proxy canary succeeds
- raw output recovery works
- mislead and token gates pass
