# Algorithm: Rank Tool Hits

## Purpose

Rank broad search output so the coding agent opens the most relevant files and
symbols first. This mode is rank-only and does not return generic cards.

## Inputs

- goal
- search terms
- recent tool result from `rg`, `grep`, or equivalent search
- already-seen state
- current structural graph

## Algorithm

```text
1. Parse tool rows into file, line, and text hits.
2. Normalize paths and group hits by file.
3. Map each line to File, Symbol, or CodeRegion when possible.
4. Score each group.
5. Suppress generated/vendor/noise hits.
6. Return ranked groups with reason codes.
```

## Score Inputs

```text
lexical overlap with goal/search_terms
line-to-symbol confidence
path role: source, test, docs, config
active-version status
structural proximity to known anchors
already-seen penalty
noise penalty
```

## Output

```json
{
  "ranked_groups": [
    {
      "rank": 1,
      "file": "src/example.py",
      "best_lines": [42],
      "mapped_symbols": ["Example.symbol"],
      "score": 0.91,
      "reason_codes": ["line_grounded", "symbol_match", "source_file"]
    }
  ],
  "suppressed": []
}
```

## Phase Gate

The mode must beat raw `rg` baseline on real-session replays:

- fewer irrelevant file opens
- faster time to correct file
- eventual edited file appears in top three
- no hot-path bootstrap
- no Qwen dependency
