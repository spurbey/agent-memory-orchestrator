# Qwen Work-Causality Contract

## Purpose

Qwen proposes semantic work causality from a structured commit/work window. It does not write graph truth.

## Input Packet

A packet must include bounded, structured sections:

```json
{
  "repo_id": "repo:example",
  "work_window_id": "work:...",
  "commit": {"sha": "...", "message": "..."},
  "user_discussion": [],
  "agent_updates": [],
  "tool_calls": [],
  "tool_observations": [],
  "hunks": [],
  "mapped_entities": [],
  "validation": [],
  "existing_graph_context": []
}
```

## Output Proposal

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

## Hard Gates

Qwen output must be valid JSON, schema-valid, evidence-grounded, and reviewed before any graph write.

## Rejection Handling

Rejected frames are stored as review artifacts with rejection reason. They do not create graph truth. Repeated rejection patterns feed Qwen prompt/schema evals.
