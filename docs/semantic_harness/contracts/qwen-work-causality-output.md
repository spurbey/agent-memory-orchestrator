# Contract: Qwen Work-Causality Output

## Purpose

Qwen output proposes work causality and relation occurrence reasons.

## Shape

```json
{
  "work_intent": {"summary": "string", "confidence": 0.0},
  "frames": [
    {
      "kind": "problem|cause|decision|fix|constraint|validation|risk|open_question",
      "statement": "string",
      "evidence_refs": [],
      "code_entity_ids": [],
      "confidence": 0.0
    }
  ],
  "relation_occurrence_candidates": [
    {
      "relation": "CO_CHANGED_WITH|MOTIVATED_BY|VALIDATED_BY|DEPENDS_ON",
      "source_id": "string",
      "target_id": "string",
      "reason": "string",
      "evidence_refs": [],
      "confidence": 0.0
    }
  ],
  "risk_hints": [],
  "test_hints": []
}
```

## Hard Rule

This output never mutates the graph directly. Semantic review owns acceptance.
