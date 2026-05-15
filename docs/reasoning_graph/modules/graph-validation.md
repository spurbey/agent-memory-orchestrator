# Graph Validation

## Depends on
- ../graph_model/node-types.md
- ../graph_model/edge-types.md
- ../architecture/05-failure-and-safety-model.md

## Used by

## Related docs
- qwen-contracts.md
- web-debug-visibility.md

## Purpose

Validate graph shape and safety gates before selecting extraction runs, applying central merge, or swapping fresh rebuilt graph.

## Inputs

Kuzu graph path, session id, extraction run id, optional commit id.

## Outputs

Validation report with pass/fail, counts, missing provenance, AST fallback metrics, Qwen diagnostics, contested count, and graph shape checks.

## Owned state

Validation reports and diagnostics.

## Public interfaces planned

- `validate_timeline(session_id)`
- `validate_session_graph(session_id, extraction_run_id)`
- `validate_central_graph()`
- `validate_rebuild_candidate(graph_path)`

## Kuzu writes

Normally none. May write validation report support node when requested.

## Failure modes

Validation errors block graph swap and central finalize. Warnings do not block but appear in CLI/API/web.

## Validation checks

Every answer node has evidence. Coding sessions have code nodes. Selected extraction run exists. Central graph has no answer-grade raw/support promotions.
