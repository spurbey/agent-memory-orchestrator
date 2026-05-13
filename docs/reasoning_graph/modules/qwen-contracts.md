# Qwen Contracts

## Depends on
- ../architecture/05-failure-and-safety-model.md
- ../graph_model/extraction-run-versioning.md

## Used by
- decision-extraction.md
- relationship-extraction.md
- central-graph-merge-engine.md
- ../algorithms/leiden-community-detection.md

## Related docs
- ../algorithms/decision-extraction.md
- ../algorithms/relationship-extraction.md
- ../algorithms/decision-deduplication.md

## Purpose

This is an API contract. Implementation must not improvise Qwen prompts, schemas, thresholds, or mutation behavior outside this document.

## Global Hard Gates

A Qwen result may affect graph state only when all are true:

1. Output is valid JSON.
2. Output matches the exact schema for the call.
3. Confidence is greater than or equal to the threshold for the call.
4. The accepted result is attached to an `ExtractionRun`.
5. The owner module records model name, timeout, prompt version, schema version, and token budget.

If any gate fails, output becomes diagnostic or review candidate only.

## Remote Batch Runtime

When local Qwen is unavailable or too slow, AMO may export a Qwen batch job for a GPU runtime such as Google Colab.

The batch runtime is compute only. It is not graph authority.

Exported job schema:

```json
{
  "job_id": "qwen_job:decision_extraction_fallback:<hash>",
  "schema_version": "qwen-batch-v1",
  "runtime": "colab_batch",
  "model": "qwen3:1.7b",
  "call": "decision_extraction_fallback",
  "payload_hash": "sha256-of-payload",
  "payload": {}
}
```

Returned result schema:

```json
{
  "job_id": "same job id",
  "schema_version": "qwen-batch-v1",
  "runtime": "colab_batch",
  "model": "qwen3:1.7b",
  "call": "decision_extraction_fallback",
  "payload_hash": "same sha256-of-payload",
  "output": {}
}
```

Local AMO must reject a batch result when `job_id`, `call`, `payload_hash`, schema version, or output schema does not match the exported job. Qwen may cite only event ids present in the exported payload or durable evidence refs attached to the `ExtractionRun`.

## Common Failure Behavior

Timeout, model unavailable, empty response, invalid JSON, schema mismatch, and low confidence must not mutate graph. Store diagnostic fields:

```json
{"call":"decision_extraction_fallback","error_type":"timeout|invalid_json|schema_mismatch|low_confidence|model_unavailable","raw_excerpt":"bounded excerpt","extraction_run_id":"..."}
```

## Call: Query Intent Planning

Owner: graph debug/retrieval module. This is not part of graph construction.

Input:

```json
{"query":"string","mode":"debug|inspection","available_scopes":["session","central","raw"]}
```

Output schema:

```json
{"intent":"general|work_history|decision_history|raw_evidence|code_why|version_flow|contested","entities":["string"],"include_raw":false,"include_historical":false,"confidence":0.0}
```

Threshold: `0.60`. Low confidence falls back to deterministic planner.

May create graph nodes/edges: none.

## Call: Decision Extraction Fallback

Owner: decision extraction module.

Input:

```json
{"session_id":"string","extraction_run_id":"string","thread_id":"string","messages":[{"role":"user|assistant|tool","text":"bounded string","event_id":"string"}],"code_nodes":[{"id":"string","file_path":"string","summary":"string"}],"tests":[{"id":"string","result":"pass|fail|unknown"}]}
```

Output schema:

```json
{"decisions":[{"decision_type":"planned_action|completed_fix|investigation_result|constraint|revert|open_question","subject":"string","predicate":"string","object":"string","reason":"string","confidence":0.0,"evidence_event_ids":["string"]}]}
```

Threshold: each decision confidence must be `>= 0.70`.

May create: `DecisionUnit`, `Bug`, `Fix`, `OpenQuestion` with `source=qwen`.

Must never create: central nodes, version edges, raw evidence, commit nodes.

## Call: Cause/Relationship Classification

Owner: relationship extraction module.

Input:

```json
{"source_decision":{"id":"string","text":"string"},"candidate_targets":[{"id":"string","kind":"string","text":"string"}],"thread_context":"bounded string","extraction_run_id":"string"}
```

Output schema:

```json
{"relations":[{"source_id":"string","target_id":"string","relation":"CAUSED_BY|REFINES|SUPERSEDED_BY|REVERTS|CONFLICTS_WITH|NONE","confidence":0.0,"reason":"string"}]}
```

Threshold: `>= 0.70`.

May create: session graph relationship edges only.

Must never change status directly. Status changes are owned by merge/versioning modules.

## Call: Ambiguous Central Merge Classification

Owner: central graph merge engine.

Input:

```json
{"draft_node":{"id":"string","kind":"string","summary":"string","subject":"string","predicate":"string","object":"string"},"central_candidate":{"id":"string","kind":"string","summary":"string","status":"string"},"deterministic_score":{"total":0.0,"string":0.0,"embedding":0.0,"structure":0.0}}
```

Output schema:

```json
{"relation":"NEW|DUPLICATE_OF|REFINES|SUPERSEDES|CONFLICTS_WITH","confidence":0.0,"reason":"string"}
```

Threshold: `>= 0.70` for mutation. Below threshold becomes review candidate.

May create: central relation edge only through merge engine after validation.

Must never create raw/session nodes or directly edit Kuzu outside merge transaction.

## Call: Community Label Generation

Owner: community module.

Input:

```json
{"community_id":"string","members":[{"id":"string","kind":"string","label":"string","summary":"string"}],"top_terms":["string"]}
```

Output schema:

```json
{"label":"string","confidence":0.0,"reason":"string"}
```

Threshold: `>= 0.60`. Low confidence uses deterministic top-term label.

May update: `Community.label` only.

Must never change membership or decision status.

## Call: Answer Compression

Owner: future retrieval. Out of scope for graph construction.

May create graph nodes/edges: none.
