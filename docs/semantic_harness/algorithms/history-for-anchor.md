# Algorithm: History For Anchor

## Purpose

Explain why or when a file, symbol, class, or behavior changed.

## Inputs

- goal
- anchor
- search terms
- version graph
- commits
- work windows
- reviewed semantic frames when available

## Algorithm

```text
1. Resolve anchor to active entity.
2. Traverse VERSION_OF and CHANGED_IN edges.
3. Collect commits and work windows.
4. Attach reviewed ReasoningFrames and RelationOccurrences.
5. Filter by task terms, source quality, and currentness.
6. Rank timeline events.
7. Return source-quality-labeled history.
```

## Output

```json
{
  "timeline": [
    {
      "commit": "",
      "work_window": "",
      "reason": "",
      "source_quality": "agent_session|pr|human_commit|imported_history",
      "confidence": 0.0
    }
  ]
}
```

## Rules

- Imported history starts lower confidence.
- Commit-message-only claims are not strong semantic truth.
- Reviewed semantic frames outrank unreviewed proposals.
- Empty semantic history returns partial status rather than invented reasons.
