# Enrichment And Embedding Eval

## Purpose

Prove that Semantic Harness can turn real repo evidence into reviewed semantic
facts, project one semantic unit per retrieval document, embed those projection
documents, and answer `context_for_anchor` with graph-grounded evidence.

Internal retrieval metrics are prerequisites only. Product value is proven only
when AMO helps on a certified non-derivable fixture where the no-AMO baseline
fails or chooses the wrong edit.

## Fixture Certification

Candidate non-derivable fixtures come from real history:

```text
revert pairs
non-obvious commit or PR rationale
guards or unusual choices explained only in history
validated agent-session decisions not visible in current code
manual intent notes when available
```

Certification:

```text
run no-AMO baseline first
if baseline recovers the reason -> derivable_or_tedious
if baseline guesses, fails, or chooses wrong edit -> certified_non_derivable
if no candidate qualifies -> fixture_missing_non_derivable
```

Only `certified_non_derivable` fixtures count as product proof.

## Product Gate

Run three lanes:

```text
no-AMO baseline
structural harness baseline
semantic harness with reviewed facts + projections + embeddings
```

Pass requires:

```text
no-AMO baseline fails to recover reason or chooses wrong edit
structural harness cannot recover it as semantic truth
semantic harness returns accepted non-derivable reason
answer has graph-grounded source refs
agent behavior changes because of the answer
```

`used_answer = true` only when:

```text
agent plan changes and references the fact content
agent avoids the baseline wrong edit
agent skips baseline exploration only needed to discover the supplied fact
```

Generic AMO mentions, independent reasoning, same-as-baseline behavior, or
continued blind exploration do not count.

## Projection And Embedding Checks

Projection/chunking:

```text
one accepted semantic fact = one projection doc
one relationship reason = one projection doc
one doc claim = one projection doc
one work-window fact = one projection doc
```

Embedding manifest checks:

```text
same doc_id + same content_hash -> reuse embedding
same doc_id + changed content_hash -> re-embed
missing doc_id -> tombstone/remove from active index
```

Normal retrieval excludes `review_only`, `rejected`, `quarantined`, and
intermediate-hypothesis facts.

## Health Metrics

These are wiring checks, not product proof:

```text
top3_expected_fact_hit >= 0.85
graph_grounding_rate = 1.0
wrong_trust_order_count = 0
stale_authoritative_leak_count = 0
rejected_fact_leak_count = 0
stable_replay_rate = 1.0
```
