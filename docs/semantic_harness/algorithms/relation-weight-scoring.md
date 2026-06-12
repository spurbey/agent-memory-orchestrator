# Relation Weight Scoring

## Purpose

Maintain aggregate relation strength while preserving occurrence-level reasons.

## Inputs

- relation kind
- participants
- commit
- hunk mappings
- validation
- semantic reason
- prior edge state

## Outputs

Updated aggregate edge weight and new RelationOccurrence.

## Algorithm

```text
1. Create occurrence for the current work event.
2. Update each participant entity's changed-commit count.
3. Compute cochange_count for the aggregate pair.
4. Compute either_changed_count = source_changed_count + target_changed_count - cochange_count.
5. Compute Jaccard = cochange_count / either_changed_count.
6. Combine Jaccard with future recency, validation, and semantic reason components.
7. Store occurrence separately from edge weight.
8. Expose only task-relevant occurrences during traversal.
```

## Confidence Scoring

Structural MVP:

```text
stored_strength =
  0.45 * cochange_jaccard
+ 0.20 * recency_score
+ 0.20 * validation_score
+ 0.15 * reason_quality_score
```

Current defaults:

```text
recency_score = 0.0
validation_score = 0.0
reason_quality_score = 0.0
```

Agent-facing historical relation cards additionally require:

```text
stored_strength >= 0.40
cochange_count >= 3
```

Task-specific occurrence filtering uses weighted lexical terms:

```text
strong domain/task term = 1.0
low-signal action term such as fix/update/change/use/set = 0.25
task_match requires matched weight >= 1.0
weak_task_match means only low-signal action terms matched
```

Strict eval modes that require task-relevant occurrence must require `task_match`, not `weak_task_match`.

## Failure Modes

Weak mappings create low-confidence occurrence but do not delete prior relation. Contradictory semantic reasons become review-only.

## Product Usage

Supports `impact_check`, `edit_plan`, and risk cards.

## Real-Session Eval

Replay repeated co-changes and verify:

```text
cochange_count <= either_changed_count
len(occurrence_ids) == cochange_count
stored_strength is derivable from score_components
two perfect co-changes do not show a historical_relation card by default
three perfect co-changes can show a historical_relation card
the word fix alone does not satisfy require_task_relevant_occurrence
```

## Worked Example

Input: `login` and `refresh` co-changed in three commits. `login` changed in three commits total. `refresh` changed in three commits total.

Intermediate:

```text
cochange_count = 3
source_changed_count = 3
target_changed_count = 3
either_changed_count = 3 + 3 - 3 = 3
jaccard = 3 / 3 = 1.0
stored_strength = 0.45 * 1.0 = 0.45
```

Output: aggregate `CO_CHANGED_WITH` edge has `stored_strength=0.45`, `cochange_count=3`, and three `RelationOccurrence` IDs. The card may be shown because it passes both the strength and minimum occurrence gates.
