# Extraction Run Manager

## Depends on
- ../graph_model/extraction-run-versioning.md
- daemon-job-queue.md

## Used by
- session-graph-builder.md
- qwen-contracts.md

## Related docs
- ../architecture/05-failure-and-safety-model.md
- graph-validation.md

## Purpose

Create, track, select, and finalize versioned extraction runs.

## Inputs

Session id, evidence range, transcript paths, algorithm versions, model versions, thresholds.

## Outputs

`ExtractionRun` node and run status transitions.

## Owned state

Run ids and selected/finalized run markers.

## Public interfaces planned

- `start_run(session_id, evidence_range) -> extraction_run_id`
- `mark_complete(run_id)`
- `select_run(run_id)`
- `finalize_run(run_id, commit_id)`

## Kuzu writes

Creates and updates `ExtractionRun` nodes and `CREATED_BY_RUN` edges.

## Failure modes

Partial output marks run `partial`. Failed validation prevents selection. Finalization without selected run fails closed.

## Validation checks

Every derived node has exactly one extraction run id.
