# Traversal Recipes

## Purpose

Define intent-specific graph traversal patterns for harness cards.

## Inputs

- intent
- resolved anchors
- budget
- active graph view
- retrieval candidates
- session state

## Outputs

Candidate card support paths and next actions.

## Algorithm

```text
edit_plan: anchors -> active versions -> structural neighbors -> relation occurrences -> risks/tests
file_context: file -> symbols/regions -> active versions -> recent changes -> risks
why_changed: active entity -> versions -> commits -> work windows -> reasoning -> validation
impact_check: edited anchors -> co-change edges -> dependencies -> tests -> risk cards
test_plan: changed anchors -> prior validations -> test files -> commands
```

## Confidence Scoring

Path confidence is the minimum of anchor, version, relation, and evidence confidence, boosted by validation support.

## Failure Modes

No exact anchor triggers lexical/vector discovery. Weak graph path returns `low_confidence`. Missing history returns `partial_structural`.

## Product Usage

Planner selects these recipes after intent correction.

## Real-Session Eval

Run each recipe against rich and partial fixtures and verify status classification.

## Worked Example

Input: `intent=why_changed`, anchor `symbol:graph_service.retrieve`.

Intermediate traversal: `SymbolVersion -> Commit -> WorkWindow -> ReasoningFrame -> Validation`.

Output: `why_changed` card confidence `0.84` with commit and test evidence.
