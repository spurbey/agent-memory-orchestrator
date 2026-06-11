# Contract: amo_harness_query

## Purpose

Single public query shape for explicit coding-agent harness calls.

## Request

```json
{
  "intent": "edit_plan|tool_overlay|file_context|why_changed|impact_check|test_plan",
  "user_goal": "string",
  "anchors": {
    "files": [],
    "symbols": [],
    "commits": [],
    "errors": [],
    "recent_tool_result": {}
  },
  "budget": {
    "max_cards": 5,
    "max_tokens": 900,
    "detail": "strict|normal|deep"
  },
  "session_state": {
    "already_seen_node_ids": [],
    "already_seen_relation_ids": [],
    "already_seen_card_ids": []
  }
}
```

## Response

```json
{
  "status": "ready|partial_structural|partial_historical|partial_coverage|low_confidence|unavailable",
  "intent_requested": "string",
  "intent_used": "string",
  "intent_correction": null,
  "cards": [],
  "next_actions": [],
  "trace": {
    "nodes": [],
    "edges": [],
    "versions": [],
    "occurrences": []
  },
  "warnings": []
}
```

## Intent Correction

```json
{
  "original_intent": "file_context",
  "corrected_intent": "impact_check",
  "augmented_intents": ["test_plan"],
  "reason": "anchor symbol has high-risk co-change edge to a validation-sensitive component",
  "confidence": 0.79
}
```

## Next Action

```json
{
  "action_type": "inspect_file|run_test|call_harness|avoid_edit|verify_assumption",
  "target": "file path, symbol, test name, or harness query",
  "reason": "one sentence",
  "priority": "required|recommended|optional"
}
```

## Card Shape

```json
{
  "type": "next_file|symbol_context|why_changed|risk|test_target|dependency|historical_relation",
  "title": "short action-oriented title",
  "why": "one sentence",
  "evidence": [
    {"node_id": "string", "kind": "File|Symbol|CodeRegion|RelationOccurrence|ReasoningFrame|Commit|HarnessCard"}
  ],
  "risk": "string",
  "confidence": 0.0,
  "next_action": "string"
}
```

Card rules:

- normal mode returns compact high-confidence cards.
- deep prose is allowed only for `detail=deep` or `why_changed`.
- vector-only evidence cannot produce a `ready` card.
- every returned card is eligible to become a `HarnessCard` node with feedback status.

## Example

```json
{
  "intent": "edit_plan",
  "user_goal": "fix signin redirect after token refresh",
  "anchors": {
    "files": [],
    "symbols": ["AuthSession.refresh_token"],
    "commits": [],
    "errors": [],
    "recent_tool_result": {}
  },
  "budget": {"max_cards": 3, "max_tokens": 600, "detail": "strict"},
  "session_state": {
    "already_seen_node_ids": [],
    "already_seen_relation_ids": [],
    "already_seen_card_ids": []
  }
}
```

Expected response status is `ready` when the anchor resolves and graph evidence supports cards. It is `partial_structural` when only structure exists.
