# Extraction Run Versioning

## Depends on
- node-types.md
- status-lifecycle.md
- provenance-and-evidence.md

## Used by
- ../modules/extraction-run-manager.md
- ../modules/qwen-contracts.md

## Related docs
- central-versioning-rules.md
- ../architecture/05-failure-and-safety-model.md

## Purpose

An `ExtractionRun` is the version boundary for derived session graph output. Raw evidence may be processed many times as algorithms improve. Each processing attempt gets its own run id and output set.

## ExtractionRun Required Fields

```json
{
  "id": "extraction_run:<session_id>:<timestamp_or_hash>",
  "session_id": "session id",
  "evidence_range": {"start_id": "raw_a", "end_id": "raw_b"},
  "transcript_paths": ["paths imported"],
  "algorithm_versions": {},
  "model_versions": {},
  "thresholds": {},
  "status": "draft|partial|failed|complete|selected|finalized",
  "diagnostics": [],
  "created_at": "ISO timestamp"
}
```

## Run Statuses

`draft`: run was planned but not complete.

`partial`: some outputs were created but validators did not pass.

`failed`: run did not produce usable output.

`complete`: run produced session graph output and passed basic validators.

`selected`: user or system selected this run as session graph truth.

`finalized`: selected run was merged into central graph.

## Versioning Rules

Raw evidence is immutable and reused across runs.

Derived nodes must include `extraction_run_id`.

A new run does not delete older run output.

Only one run per session can be selected for finalization at a time.

Central merge must record which extraction run was finalized.

## Re-Extraction

If algorithms improve, create a new extraction run over the same raw evidence range. The new run can be selected later. The old run remains inspectable for audit and comparison.
