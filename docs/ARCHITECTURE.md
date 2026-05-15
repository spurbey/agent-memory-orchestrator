# Architecture

AMO is local-first graph memory for AI coding sessions.

## Runtime Shape

- Hooks capture evidence and fail open.
- The daemon owns Kuzu, graph jobs, retrieval, local model calls, and web UI state.
- MCP exposes explicit retrieval tools to agents.
- Kuzu stores graph truth.
- SQLite stores retrieval/index ledgers.
- FAISS is a rebuildable vector cache.
- Ollama/Qwen is used for local reasoning extraction where configured.

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
