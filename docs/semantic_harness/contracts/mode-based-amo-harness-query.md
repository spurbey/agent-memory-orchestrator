# Contract: Mode-Based `amo_harness_query`

## Purpose

Define the target request and response contract for the single public Semantic
Harness MCP tool.

## Request

```json
{
  "mode": "context_for_anchor",
  "goal": "string",
  "search_terms": [],
  "anchors": {
    "files": [],
    "symbols": [],
    "code_regions": [],
    "commits": [],
    "tests": [],
    "errors": []
  },
  "questions": [],
  "recent_tool_result": {
    "kind": "rg|grep|file_read|git_diff|test_output|apply_patch|mcp_result|unknown",
    "text": "",
    "parsed_refs": []
  },
  "planned_edits": [],
  "constraints": {
    "prefer_current_version": true,
    "include_history": false,
    "include_tests": "only_if_answer_relevant",
    "include_dependencies": "only_if_answer_relevant",
    "max_depth": 2
  },
  "budget": {
    "max_results": 8,
    "max_tokens": 700,
    "detail": "rank_only|strict|normal|deep"
  }
}
```

## Compatibility

Existing callers may send `intent`. The compatibility layer maps it to `mode`:

```text
file_context -> context_for_anchor
tool_overlay -> rank_tool_hits when a search result is present
impact_check -> pre_edit_review when planned edits exist
why_changed -> history_for_anchor
test_plan -> pre_edit_review with validation focus
edit_plan -> context_for_anchor or pre_edit_review based on anchors/planned edits
```

If mapping is ambiguous, the response includes a compatibility warning and uses
the safest narrow mode.

## Response Envelope

```json
{
  "status": "ready|partial_structural|partial_historical|partial_coverage|low_confidence|clarification_needed|unavailable",
  "mode_requested": "string",
  "mode_used": "string",
  "result": {},
  "trace": {
    "anchors": [],
    "question_classifications": [],
    "nodes": [],
    "edges": [],
    "versions": [],
    "occurrences": [],
    "reason_codes": []
  },
  "warnings": []
}
```

## Mode Result Shapes

`rank_tool_hits` returns:

```json
{
  "ranked_groups": [],
  "suppressed": []
}
```

`context_for_anchor` returns:

```json
{
  "answers": [
    {
      "question": "what will break if I change this?",
      "question_type": "risk",
      "answer": "Do not merge raw and unique edge counts; a prior diagnostic proved duplicate observations are expected.",
      "confidence": 0.82,
      "fact_type": "risk_or_impact",
      "derivability": "requires_git_history",
      "review_status": "accepted",
      "discovery_cost": "high",
      "evidence": []
    }
  ],
  "invariants": [],
  "action_relevant_links": [],
  "recommended_next_mode": null
}
```

`derivability` is required for semantic answers:

```text
derivable_from_current_code
derivable_from_docs
requires_git_history
requires_agent_session_history
requires_human_intent
requires_runtime_observation
mixed
unknown
```

Facts that require history, session evidence, human intent, or runtime
observation are the product-value layer. Facts derivable from current code or
docs are useful shortcuts, but they must not be counted as decisive AMO product
proof.

`pre_edit_review` returns:

```json
{
  "decision": "go|edit_with_warnings|blocked",
  "must_inspect": [],
  "tests_to_run": [],
  "risk_findings": [],
  "do_not_do": []
}
```

`relationship_between_anchors` returns:

```json
{
  "relationship_paths": [],
  "missing_or_weak_links": []
}
```

`history_for_anchor` returns:

```json
{
  "timeline": [],
  "source_quality": []
}
```

`semantic_diff` returns:

```json
{
  "changed_entities": [],
  "unexpected_changes": [],
  "patch_risks": [],
  "tests_to_run": []
}
```
