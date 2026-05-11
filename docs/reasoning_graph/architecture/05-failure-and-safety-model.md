# Failure And Safety Model

## Depends on
- ../README.md
- 03-runtime-ownership.md

## Used by
- ../modules/qwen-contracts.md
- ../modules/daemon-job-queue.md
- ../implementation/09-test-and-acceptance-gates.md

## Related docs
- ../graph_model/status-lifecycle.md
- ../modules/graph-validation.md
- ../modules/kuzu-graph-store.md

## Safety Invariant

AMO must fail safe. A failure may produce diagnostics, review candidates, or incomplete draft extraction runs. A failure must not corrupt raw evidence, delete historical graph knowledge, or silently promote uncertain knowledge into central memory.

## Hook Failures

Hooks fail open. If AMO home is blocked, hooks write to fallback spool. If writing fails, hooks return control to the agent and record best-effort diagnostics if possible. Hooks must never block on Qwen, Kuzu, embeddings, Tree-sitter, or network services.

## Daemon Queue Failures

Queued jobs must be idempotent. A retry must use the same session id, evidence id range, extraction run id, and commit id. Job failure records a diagnostic state and leaves previous successful extraction runs untouched.

## Qwen Failures

Timeout, unavailable model, empty response, invalid JSON, schema mismatch, and low confidence all fail closed. The result becomes a diagnostic or review candidate. It must not create answer-grade graph nodes or central version edges.

## Tree-Sitter Failures

Missing grammar or parse failure does not stop graph build. The code hunk becomes a `CodeNode` with `ast_status=unparsed`. The validator must report fallback counts by language and file extension so grammar gaps are visible.

## Embedding Failures

If embeddings are unavailable, algorithms requiring semantic similarity cannot claim high-confidence decisions. They either use deterministic fallback with lower confidence or create review candidates. Any score using missing embeddings must record `embedding_status=missing`.

## Kuzu Lock Failures

The daemon should own the normal Kuzu connection. CLI/MCP graph commands should use daemon endpoints. Offline commands that open Kuzu directly may fail on locks and must report that clearly instead of falling back to stale cache data.

## Partial Extraction

An `ExtractionRun` can have status `failed`, `partial`, `complete`, or `selected`. Partial runs are inspectable but cannot be finalized into central graph unless validators explicitly pass required minimum graph shape.

## Fresh Graph Rebuild Rollback

Fresh rebuild must write to a new graph path. The active graph is swapped only after validation passes. The old graph is backed up before swap. Failed rebuilds leave active graph untouched.

## Low-Confidence Merge

Ambiguous central merge relations below threshold are review candidates. They do not mutate central graph. The central graph must prefer no relation over a wrong relation.

## Contested Decisions

Dependency propagation can mark downstream nodes `contested_pending_review`. These must surface in CLI/API/web diagnostics and session startup briefing. Contested decisions must not silently accumulate.

## No-Delete Rule

Raw evidence, extraction runs, central decisions, code nodes, and version nodes are not deleted by normal graph operations. Statuses and edges express lifecycle changes.

## Diagnostic Surfaces

Every failure category must be visible in at least one CLI command, one daemon API response, and the web debug surface. Validation commands must fail if a silent failure would otherwise pass.