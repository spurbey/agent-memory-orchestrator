# Example: rg Tool Overlay

## Input

Agent runs `rg "GraphView" src tests` and receives many hits.

## Harness Query

```json
{
  "intent": "tool_overlay",
  "user_goal": "find the active central memory path",
  "anchors": {
    "recent_tool_result": {
      "tool": "rg",
      "query": "GraphView",
      "hits": ["..."]
    }
  },
  "budget": {"max_cards": 3, "max_tokens": 500, "detail": "strict"},
  "session_state": {"already_seen_node_ids": [], "already_seen_relation_ids": [], "already_seen_card_ids": []}
}
```

## Expected Output

Cards should identify the few files most related to active GraphView retrieval and warn against following unrelated UI/debug hits first.
