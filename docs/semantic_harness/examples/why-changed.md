# Example: Why Changed

## Goal

Explain why a file or symbol changed.

## Traversal

```text
active SymbolVersion
-> Commit
-> WorkWindow
-> ReasoningFrame
-> Validation
-> RelationOccurrence
```

## Output Rule

The card must cite commit and code evidence. If the path exists only through vector candidates, return `low_confidence`.
