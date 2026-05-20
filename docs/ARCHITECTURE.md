# Architecture

AMO is local-first graph memory for AI coding sessions.

## Runtime Shape

- Hooks capture evidence and fail open.
- The daemon owns session-boundary drain, V2 session jobs, Kuzu, retrieval, local model calls, and web UI state.
- MCP exposes explicit retrieval tools to agents.
- Kuzu stores graph truth.
- SQLite stores retrieval/index ledgers.
- FAISS is a rebuildable vector cache.
- Ollama/Qwen is used for local reasoning extraction where configured.

## Automatic Session Processing

```text
hook evidence
-> append-only JSONL
-> daemon drain loop
-> trigger only when a later session starts
-> enqueue durable V2 session job in SQLite
-> evidence view and commit-backed work packets
-> packet-wise Qwen reasoning extraction when available
-> deterministic hunks, AST/code nodes, symbols, and code links
-> V2 Kuzu graph write
-> retrieval document rebuild
-> bounded embedding/FAISS refresh
```

Hooks never perform graph work directly. Drain does not run Qwen, Kuzu writes,
retrieval rebuild, embeddings, or old `GraphDelta` processing. It reads raw
evidence, tracks cursors/session boundaries, and idempotently enqueues the
previous session when a new `session_start` appears.

The V2 job runner owns resumable processing and stores job/stage/event state in
SQLite. If Qwen or the embedding model is unavailable, the job pauses as
`pending_model`; it does not create fake answer-grade reasoning or hash-vector
production output.

Old pre-V2 graph/retrieval data is cleaned only through an explicit backup-first
operator command:

```bash
amo-cli v2-reset-production --backup --clean-graph --clean-retrieval
```

The reset command never deletes raw JSONL evidence, config, or V2 job tables.
It writes the production reset marker only after both graph and retrieval/vector
storage have been cleaned from a verified backup; backup-only runs do not unlock
V2 production writes.
The legacy `GraphDelta` path is isolated to `graph-drain-smoke` and writes to a
disposable graph under `.state/smoke/`, not the production Kuzu path.

## Retrieval Shape

```text
query
-> exact + BM25 + vector candidates
-> deterministic fusion
-> graph neighborhood expansion
-> optional cross-encoder rerank
-> answer with packet, commit, evidence, and code citations
```

## Safety Rules

- No automatic prompt memory injection.
- Raw hooks and transcript payloads remain provenance unless promoted through validation.
- Model downloads are explicit setup actions.
- Runtime state, evidence, graph stores, logs, and exports stay out of Git.

See [Reasoning Graph V2](./reasoning_graph/README.md) for graph model details.
