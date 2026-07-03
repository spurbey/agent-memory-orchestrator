# Algorithm: Pre-Edit Impact Reviewer

## Purpose

Review planned edits before patching. The reviewer catches missed files, tests,
hidden coupling, risky assumptions, and forbidden semantic drift.

## Inputs

- goal
- planned edits
- anchors
- questions
- structural graph
- reviewed semantic evidence when available

## Algorithm V1: Structural

```text
1. Ground planned edits to File, Symbol, or CodeRegion nodes.
2. Expand action-relevant impact frontier.
3. Include callers/importers/tests/docs/config only when relevant to the planned edit.
4. Score structural risk.
5. Return go, edit_with_warnings, or blocked.
```

## Risk Signals

```text
fan-in
validation criticality
public API or persistence role
co-change strength with minimum occurrence count
uncertainty from weak anchor mapping
already verified tests
```

## Algorithm V2: Semantic

Adds:

- reviewed RelationOccurrence reasons
- accepted risk/test hints
- similar past work
- source-quality-aware history

## Output

```json
{
  "decision": "edit_with_warnings",
  "must_inspect": [],
  "tests_to_run": [],
  "risk_findings": [],
  "do_not_do": []
}
```

## Phase Gate

The mode must beat no-AMO baseline on planned-edit fixtures and must not claim
semantic risks without accepted semantic evidence.
