# Algorithm: Question Classification

## Purpose

Route `context_for_anchor` questions to the minimum graph evidence needed to
answer them. This prevents generic file profiles and raw dependency dumps.

## Inputs

- goal
- anchors
- questions
- search terms
- recent tool context
- budget

## Output

```json
{
  "question": "why does this exist and what will I break if I change it?",
  "types": ["history", "risk"],
  "confidence": 0.82,
  "reason_codes": ["why_exist", "break_if_change"]
}
```

## Types

```text
semantic_role
invariant
validation
risk
local_relation
history
usage
unknown
```

## Routing Rules

- "responsible for", "role", "what is this" -> `semantic_role`
- "invariant", "guarantee", "must stay true" -> `invariant`
- "test", "validate", "verify" -> `validation`
- "break", "risk", "affected" -> `risk`
- "relate", "connect", "link to" -> `local_relation`
- "why", "when", "changed", "created" -> `history`
- "calls", "uses", "called by" -> `usage`

Questions may map to multiple types. Multi-type questions run bounded routes
with shared budget.

## Failure Modes

- no useful question -> `clarification_needed`
- too broad -> recommend a narrower question
- cross-anchor relationship -> recommend `relationship_between_anchors`
- deep history -> recommend `history_for_anchor`

## Eval Fixture Requirements

Use real agent questions and expected types:

- single type
- multi-type
- unknown
- too broad
- deeper-mode recommendation

The classifier must expose reason codes so routing errors are debuggable.
