# Daemon Job Queue

## Depends on
- ../architecture/03-runtime-ownership.md
- ../architecture/05-failure-and-safety-model.md

## Used by
- extraction-run-manager.md
- central-graph-merge-engine.md

## Related docs
- graph-validation.md
- kuzu-graph-store.md
- ../graph_model/extraction-run-versioning.md

## Purpose

Run heavy graph work outside hooks with retries, idempotency, and diagnostics.

## Inputs

Stop/finalize trigger, manual CLI/API request, or rebuild request.

## Outputs

Queued jobs, job states, diagnostics, and completed graph processing results.

## Owned state

Queue records keyed by job type, session id, evidence range, extraction run id, commit id, and graph path.

## Job identity and states

Every daemon job must have a durable idempotency key. Re-enqueueing the same work must return the existing job instead of creating duplicate graph output.

Idempotency key fields:

```json
{
  "job_type": "build_session|finalize_session|rebuild_central|cluster_central",
  "session_id": "session id or empty for central jobs",
  "evidence_range": {"start_id": "raw_a", "end_id": "raw_b"},
  "transcript_paths_hash": "stable hash of imported transcript paths",
  "extraction_run_id": "run id when known",
  "commit_id": "commit sha or empty",
  "candidate_graph_path": "path for rebuild jobs",
  "algorithm_version": "reasoning graph algorithm version"
}
```

Job states:

```text
queued      accepted but not started
running     worker acquired job and heartbeat is fresh
partial     worker wrote partial ExtractionRun or candidate graph output
retrying    failure is retryable and backoff is active
failed      retry budget exhausted or validation made the job terminal
complete    job finished and validators accepted the output
stale       running heartbeat expired before completion
abandoned   superseded by a newer idempotency key or manual cancellation
```

The queue record stores:

- `job_id`
- `idempotency_key`
- `state`
- `attempt`
- `created_at`
- `started_at`
- `updated_at`
- `heartbeat_at`
- `last_error`
- `diagnostics`
- output pointers such as `extraction_run_id`, `merge_plan_id`, or `candidate_graph_path`

## Public interfaces planned

- `enqueue_session_build(session_id, evidence_range)`
- `enqueue_finalize(session_id, extraction_run_id, commit)`
- `job_status(job_id)`
- `resume_stale_jobs()`
- `mark_stale(job_id, reason)`

## Kuzu writes

None directly except job diagnostic nodes if configured. Worker modules perform graph writes.

## Failure modes

Retries use exponential backoff. Permanent failure records diagnostic. Duplicate enqueue returns existing job.

## Crash recovery

Daemon crash recovery is explicit, not implied.

Heartbeat rule:

- A running worker updates `heartbeat_at` while processing.
- If `state=running` and `heartbeat_at` is older than the configured stale threshold, the next daemon startup marks the job `stale`.
- Default stale threshold should be longer than the largest allowed Qwen timeout plus graph write timeout.

Session build recovery:

1. Load stale job by idempotency key.
2. Inspect its `ExtractionRun`.
3. If the run is `complete` or `selected`, mark job `complete`; do not rerun.
4. If the run is `partial`, resume only steps that are idempotent for that `extraction_run_id`.
5. If partial outputs cannot be safely resumed, create a new `ExtractionRun` with a new run id and link it to the stale run with `REPLACES_RUN`.
6. Never mark a partial run `selected` unless `graph-validate-session` passes required minimum graph shape.

Finalize recovery:

1. Finalize jobs must use a durable `merge_plan_id`.
2. Dry-run merge plan creation is idempotent and can be reused after restart.
3. Apply phase must check whether each planned node/edge already exists before writing.
4. If apply was interrupted, rerun apply from the same `merge_plan_id` and skip already-applied operations.
5. A finalize job cannot complete unless central graph validation passes for the affected commit/session slice.

Fresh rebuild recovery:

1. Rebuild jobs write to a candidate graph path, never the active graph path.
2. If the daemon crashes, the candidate graph is marked incomplete.
3. Resume can continue the same candidate path only if validation confirms the candidate graph is internally consistent for completed jobs.
4. Otherwise, resume creates a new candidate graph path.
5. An incomplete candidate graph must never swap into active graph.

Retry policy:

```text
retryable:
  Qwen timeout, temporary Kuzu lock, embedding runtime unavailable, daemon restart

terminal:
  invalid input schema, missing raw evidence, missing transcript path, graph validation failure after retries
```

Partial-run safety:

- Partial output is inspectable.
- Partial output is not central-merge eligible.
- Partial output must be clearly marked in CLI/API/web diagnostics.
- Validators must treat stale or partial extraction runs as failed unless explicitly running a repair command.

Stale job diagnostics:

Every stale or resumed job must expose:

- original `job_id`
- idempotency key
- stale reason
- last heartbeat
- partial output pointers
- chosen recovery action: `resume_same_run`, `new_extraction_run`, `resume_merge_plan`, `new_candidate_graph`, or `fail_closed`

## Validation checks

Same job can run twice without duplicate graph nodes.

Daemon restart with a running job produces exactly one of:

- completed existing output
- resumed partial output
- new replacement extraction run
- failed diagnostic

It must not produce duplicate selected extraction runs or duplicate central promotions.
