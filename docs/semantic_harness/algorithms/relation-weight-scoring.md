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
2. Score occurrence confidence from mapping, semantic reason, validation, and participant reliability.
3. Apply recency and frequency update to aggregate edge.
4. Store occurrence separately from edge weight.
5. Expose only task-relevant occurrences during traversal.
```

## Confidence Scoring

Occurrence confidence equals mapping `0.35`, semantic reason `0.25`, validation `0.20`, and co-change strength `0.20`. Structural-only occurrences omit semantic reason weight.

## Failure Modes

Weak mappings create low-confidence occurrence but do not delete prior relation. Contradictory semantic reasons become review-only.

## Product Usage

Supports `impact_check`, `edit_plan`, and risk cards.

## Real-Session Eval

Replay a session with repeated file co-changes and verify aggregate weight rises while cards cite only relevant occurrences.

## Worked Example

Input: `AuthSession.refresh` and `LoginButton.onSubmit` co-changed in commit `abc123` with accepted reason `signin redirect fix`. Mapping `0.91`, semantic `0.82`, validation `0.70`, co-change `0.80`.

Intermediate score: `0.91*0.35 + 0.82*0.25 + 0.70*0.20 + 0.80*0.20 = 0.82`.

Output: occurrence confidence `0.82` and updated edge weight `0.76`.
