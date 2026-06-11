# Intent Correction

## Purpose

Validate, override, or augment the intent requested by the coding agent.

## Inputs

- requested intent
- user goal
- anchors
- recent tool result
- session state
- risk flags
- available graph evidence

## Outputs

Intent decision: accept, override, augment, low confidence, or unavailable. Output includes correction reason and confidence.

## Algorithm

```text
1. Score requested intent against user goal and anchors.
2. Detect hard triggers such as failing tests, risky co-change edges, or direct file/symbol anchor.
3. If requested intent matches, accept.
4. If another intent is safer and confidence >= 0.75, override.
5. If requested intent is useful but incomplete, augment.
6. If evidence conflicts or is weak, keep requested intent and return low_confidence warning.
```

## Confidence Scoring

Combine anchor match 0.35, goal/intent lexical match 0.20, graph risk trigger 0.25, and recent tool result fit 0.20.

## Failure Modes

Unknown intent returns `unavailable`. Ambiguous correction returns requested intent with `low_confidence`. Missing anchors falls back to goal-only planning.

## Product Usage

Prevents the agent from asking for `file_context` when `impact_check` or `test_plan` is needed for safe work.

## Real-Session Eval

Use a real session where an opened file has a high-risk co-change edge and verify `file_context` is augmented with `impact_check`.

## Worked Example

Input: `intent=file_context`, anchor `symbol:AuthSession.refresh`, goal `fix signin redirect`. Graph has `CO_CHANGED_WITH` edge to `LoginButton.onSubmit` weight `0.83` and prior validation test.

Intermediate score: anchor match `0.35`, goal fit `0.16`, risk trigger `0.25`, tool fit `0.03`, total `0.79`.

Output: `corrected_intent=impact_check`, `augmented_intents=["test_plan"]`, confidence `0.79`.
