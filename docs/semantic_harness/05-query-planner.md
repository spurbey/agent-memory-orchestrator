# Query Planner

## Purpose

The query planner turns an agent request into a safe retrieval and traversal plan.

## Planner Steps

```text
1. Normalize user_goal and anchors.
2. Classify or confirm requested intent.
3. Correct or augment intent when evidence requires it.
4. Resolve exact anchors.
5. Pick traversal recipe.
6. Allocate budget across cards, traces, and warnings.
7. Execute retrieval stack.
8. Build strict cards and next actions.
9. Record HarnessCard feedback candidate.
```

## Intent Correction

If the agent sends the wrong intent, the harness may accept, override, augment, or return low confidence.

Correction example:

```json
{
  "original_intent": "file_context",
  "corrected_intent": "impact_check",
  "augmented_intents": ["test_plan"],
  "reason": "anchor symbol has high-risk co-change edge to a validation-sensitive component",
  "confidence": 0.79
}
```

## Planner Safety Rules

- Do not use vector-only results as trusted cards.
- Do not return raw history unless the intent asks for history.
- Do not exceed `max_cards` or `max_tokens`.
- Prefer active versions over historical versions.
- Mark weak candidate paths as `low_confidence`.
- Return `unavailable` when no grounded path exists.

## Status Selection

- `ready`: exact or strongly grounded graph context exists.
- `partial_structural`: structure exists but work history is missing.
- `partial_historical`: history exists but current structure is incomplete.
- `partial_coverage`: only some anchors resolved.
- `low_confidence`: candidates exist but graph grounding is weak.
- `unavailable`: no trusted context.
