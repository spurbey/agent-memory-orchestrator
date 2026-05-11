# Runtime Ownership

## Depends on
- 02-three-level-storage.md
- 05-failure-and-safety-model.md

## Used by
- ../modules/daemon-job-queue.md
- ../modules/qwen-contracts.md
- ../implementation/00-implementation-principles.md

## Related docs
- ../modules/kuzu-graph-store.md
- ../modules/raw-evidence-ledger.md
- ../modules/session-graph-builder.md

## Ownership Rule

Hooks capture. The daemon processes. CLI/API/MCP control and inspect.

This split is mandatory because Qwen, embeddings, Tree-sitter parsing, Kuzu writes, clustering, and central merge can be slow or fail. Hooks must not block agent work.

## Hook Responsibilities

Hooks may:

- Receive `SessionStart`, `UserPromptSubmit`, `PostToolUse`, and `Stop` payloads.
- Redact obvious secrets.
- Append raw evidence to `AMO_HOME/.evidence` or fallback spool.
- Return capture status.

Hooks must not:

- Open Kuzu.
- Call Qwen.
- Compute embeddings.
- Run Tree-sitter.
- Merge central graph nodes.
- Rebuild indexes.

## Daemon Responsibilities

The daemon owns:

- Kuzu database connection.
- Evidence drain cursors.
- Job queue.
- Codex transcript import.
- Timeline construction.
- Chunking and session graph build.
- Qwen calls.
- Embedding calls.
- Git diff/code analysis.
- Central graph merge.
- Dependency propagation.
- Leiden clustering.
- Graph validation.
- Web/API diagnostics.

## CLI/API/MCP Responsibilities

CLI, API, and MCP should call daemon endpoints by default. They should not silently open Kuzu if the daemon is unavailable, because embedded graph locks can corrupt user expectations and create inconsistent reads.

Offline diagnostic commands can exist, but they must clearly state when they are opening Kuzu directly and may fail on locks.

## Job Queue Rule

Stop/finalize should enqueue graph processing work. The queue must be idempotent by session id, evidence id range, extraction run id, and commit id. Retrying a job must not duplicate graph nodes.