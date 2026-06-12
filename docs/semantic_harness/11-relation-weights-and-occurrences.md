# Relation Weights And Occurrences

## Purpose

The harness must distinguish relation strength from task-specific relevance.

## Model

Aggregate edge:

```text
A CO_CHANGED_WITH B
weight = 0.76
```

Occurrence evidence:

```text
RelationOccurrence 1:
  commit = abc123
  reason = signup redirect fix

RelationOccurrence 2:
  commit = def456
  reason = token refresh bug

RelationOccurrence 3:
  commit = ghi789
  reason = onboarding state cleanup
```

## Update Algorithm

When a commit touches related entities:

```text
1. Identify participants from hunk mappings.
2. Create structural co-change occurrence.
3. If semantic review accepts a reason, attach reason to occurrence.
4. Update aggregate edge weight with decay and confidence.
5. Store occurrence as queryable provenance.
```

## Scoring Inputs

- co-change frequency
- recency
- validation linkage
- semantic reason confidence
- shared work window
- shared tests
- relation kind reliability

## Current Structural Score

The structural MVP uses denominator-aware co-change strength:

```text
jaccard = cochange_count / either_changed_count

stored_strength =
  0.45 * cochange_jaccard
+ 0.20 * recency_score
+ 0.20 * validation_score
+ 0.15 * reason_quality_score
```

Until semantic enrichment exists:

```text
recency_score = 0.0
validation_score = 0.0
reason_quality_score = 0.0
```

This intentionally keeps early structural-only scores conservative.

## Agent-Facing Historical Relation Gate

Do not show a `historical_relation` card from strength alone. Small histories can produce a perfect Jaccard score by accident.

Default gate:

```text
stored_strength >= 0.40
cochange_count >= 3
```

The `cochange_count >= 3` requirement is part of the product contract. A single co-change, or two co-changes in a tiny repo history, must not be shown as high-confidence agent guidance.

The thresholds must be configurable for evals and probes. Any non-default threshold must be visible in card evidence or eval output.

## Traversal Rule

Retrieve aggregate edges for speed, then filter occurrences by task relevance. Cards should cite the few relevant occurrences, not the entire history.
