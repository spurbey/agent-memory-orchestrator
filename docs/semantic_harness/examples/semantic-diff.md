# Example: Semantic Diff

## Goal

Explain what changed semantically between two versions.

## Inputs

- old SymbolVersion or CodeRegionVersion
- new SymbolVersion or CodeRegionVersion
- hunk mapping
- accepted ReasoningFrames if available

## Output

```text
changed behavior
supporting hunk
reason if accepted
validation evidence
risk if relation occurrences indicate impact
```

If Qwen enrichment is missing, output structural diff with `partial_structural`.
