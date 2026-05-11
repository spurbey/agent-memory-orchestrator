# Provenance And Evidence

## Depends on
- node-types.md
- edge-types.md

## Used by
- ../modules/raw-evidence-ledger.md
- ../modules/session-graph-builder.md
- ../modules/graph-validation.md

## Related docs
- extraction-run-versioning.md
- central-versioning-rules.md
- ../architecture/05-failure-and-safety-model.md

## Provenance Chain

Every answer-grade graph node must trace to:

```text
RawEvidenceRef
  -> TimelineEvent
  -> cleaned evidence or decision thread
  -> ExtractionRun
  -> session graph node
  -> central graph node when promoted
  -> GitCommit when code-linked
```

## Required Evidence Fields

Answer-grade nodes must include:

- `evidence_ids`
- `extraction_run_id`
- `session_id`
- `source_app`
- `created_at`
- `confidence`
- `source`

Code-linked nodes additionally require:

- `file_path`
- `line_range`
- `commit_id` when committed
- `patch_id` when committed

## Evidence Boundaries

Raw evidence is not answer memory. Cleaned evidence is not answer memory. A decision, fix, code node, or validated test can be answer-grade only when extracted with provenance and passed gates.

## Validation Rules

A node without evidence ids cannot be promoted.

A Qwen-derived node without extraction run id cannot be promoted.

A central node without provenance must be quarantined as invalid support data.

A graph inspection response must cite node id and evidence id. Code explanations must also cite file path and commit id when available.