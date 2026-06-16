# Algorithm: Context For Anchor

## Purpose

Answer a specific semantic question about a known file, symbol, or code region.
This mode is not a file profile and not an LSP replacement.

## Inputs

- goal
- anchors
- questions
- constraints
- budget

## Algorithm

```text
1. Resolve exact anchors.
2. Classify each question.
3. Select only graph/doc/history routes needed by the question types.
4. Retrieve graph-grounded evidence.
5. Suppress raw dependency dumps unless explicitly requested.
6. Return semantic-first answer snippets and selective action links.
7. Recommend a deeper mode if the question exceeds local context.
```

## Question Routes

```text
semantic_role -> summaries, docs, docstrings, accepted frames
invariant -> constraints, tests, accepted decisions, docstrings
validation -> validation edges, test files, validation frames
risk -> action-relevant callers/importers, co-change, risk hints, API/persistence role
local_relation -> bounded path between anchor and mentioned entity
history -> versions, commits, work windows, reviewed frames
usage -> selective callers/importers only when explicitly asked
```

## Output

```json
{
  "answers": [
    {
      "question": "what invariant does this maintain?",
      "answer": "Duplicate raw observations should not change structural identity.",
      "confidence": 0.78,
      "evidence": []
    }
  ],
  "invariants": [],
  "action_relevant_links": [],
  "recommended_next_mode": null
}
```

## Rules

- Do not dump all imports, callers, dependencies, or same-directory files.
- Include structural links only when they answer the question.
- Return `partial_structural` when only structure exists.
- Return `partial_historical` when history exists but current structure is weak.
- Return `clarification_needed` when no useful question is supplied.

## Phase Gate

The eval must use semantic misunderstanding fixtures, not navigation-only tasks.
The mode passes only if it prevents wrong edits or reduces discovery work versus
a no-AMO baseline.
