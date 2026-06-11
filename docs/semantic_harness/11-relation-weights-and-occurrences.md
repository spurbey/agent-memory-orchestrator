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

## Traversal Rule

Retrieve aggregate edges for speed, then filter occurrences by task relevance. Cards should cite the few relevant occurrences, not the entire history.
