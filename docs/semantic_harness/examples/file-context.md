# Example: File Context

## Goal

Explain one file before editing.

## Expected Card

```text
Inspect src/.../retrieval/query.py
Why: active retrieval path combines exact/BM25/vector candidates before graph expansion.
Evidence: FileVersion, SymbolVersion, prior WorkWindow, tests.
Confidence: 0.84
```

If no history exists, status should be `partial_structural` and evidence should cite imports, definitions, and calls only.
