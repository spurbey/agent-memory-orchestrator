# Contract: Harness Card Response

## Card Shape

```json
{
  "type": "next_file|symbol_context|why_changed|risk|test_target|dependency|historical_relation|doc_support",
  "title": "Inspect AuthSession.refresh_token",
  "why": "Changed in prior signin/session fixes and connected to login submit behavior.",
  "evidence": [
    {"node_id": "symbol:repo:src/auth/session.py:AuthSession.refresh_token:method", "kind": "Symbol"},
    {
      "node_id": "relocc:repo:abc123:CO_CHANGED_WITH:...",
      "kind": "RelationOccurrence",
      "task_relevance": "task_match|structural_fallback",
      "matched_terms": "signin,redirect"
    }
  ],
  "risk": "Co-changes with LoginButton.onSubmit in validated signin work.",
  "confidence": 0.87,
  "next_action": "Open src/auth/session.py and inspect AuthSession.refresh_token before editing login UI."
}
```

## Rules

- Evidence must cite graph IDs.
- `why` is one sentence in strict mode.
- `confidence` is required.
- Cards with only vector evidence must not be `ready` cards.
- Cards are stored as HarnessCard nodes for feedback.
- Historical relation cards cite task-matching occurrences first and label structural fallback occurrences explicitly.
- `doc_support` cards must cite a `DocSection` or `DocString` plus an exact `DOCUMENTS_*` or `MENTIONS_*` edge.
