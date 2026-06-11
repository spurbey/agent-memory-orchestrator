# Sidecar Interception Future

## Purpose

Automatic sidecar annotation can eventually enrich raw tool results before the agent asks. It is not enabled in the first implementation.

## Architectural Constraints

Explicit query mode and sidecar mode must share the same planner.

Tool results must be representable as `recent_tool_result` in `amo_harness_query`.

Sidecar annotations must use the same HarnessCard contract.

Session state must suppress repeated nodes, relations, and cards.

## Activation Gate

Automatic injection is disabled until all are true:

```text
mislead_rate <= 0.05
strict_card_precision >= 0.85
agent-visible token overhead stays within budget
real-session eval passes on rich and partial fixtures
```

## Sidecar Output Rule

Sidecar mode may attach cards but must not block raw tool results. If card confidence is weak, it should attach no card and record an eval event.
