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

The first shadow implementation uses a conservative broad-search focus card:

```text
input:
  rg output with many graph-grounded file hits

rank signals:
  path role: source/test/docs/config prior based on query intent
  query-token overlap: command/search expression tokens against file path, label, and summary

attach:
  only when at least two meaningful query terms exist
  only when top candidates have non-zero query-token focus
  never when the card would only echo every visible file with the same weak score

output:
  one compact next_file card with up to three focused files
```

This is candidate ordering, not causality. Historical relation cards require `CO_CHANGED_WITH` and `RelationOccurrence` evidence from commit updates.
