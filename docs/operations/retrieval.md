# Retrieval Pipeline

AMO V2 retrieval answers why-code-changed questions from graph truth, not raw chat logs.

## Pipeline

```text
query
-> query classifier
-> exact + BM25 + vector candidates
-> deterministic fusion
-> graph neighborhood expansion for top candidates
-> cross-encoder rerank top candidates when configured
-> answer with packet/commit/evidence/code citations
```

## Build the Retrieval Index

```bash
amo-cli graph-retrieval-build
amo-cli graph-retrieval-embed --model BAAI/bge-m3
```

## Retrieve

```bash
amo-cli graph-retrieve --query "why did this code change?" --require-vector
```

Enable cross-encoder reranking:

```bash
AMO_RERANKER_BACKEND=cross-encoder amo-cli graph-retrieve --query "why did this code change?" --require-vector
```

Windows PowerShell:

```powershell
$env:AMO_RERANKER_BACKEND="cross-encoder"
amo-cli graph-retrieve --query "why did this code change?" --require-vector
```

Expected response shape:

```json
{
  "ok": true,
  "retrieval": {
    "intent": "code_why",
    "vector_status": "faiss:completed",
    "reranker": "deterministic+bi_encoder+cross_encoder",
    "hits": []
  },
  "answer": {
    "text": "AMO indexed graph answer: ...",
    "citations": []
  }
}
```

## Citation Contract

Every answer should be traceable to graph support:

- packet IDs
- commit SHAs
- evidence IDs
- code node IDs
- code node labels
- neighbor node IDs

If a result cannot cite graph support, treat it as not ready for answer-grade retrieval.

## Storage

| Store | Role |
| --- | --- |
| Kuzu | Source of truth for graph nodes and edges |
| SQLite FTS | Canonical retrieval docs and embedding ledger |
| FAISS | Rebuildable vector cache |
| Raw evidence JSONL | Append-only provenance |

Kuzu remains graph truth. FAISS can be deleted and rebuilt.
