# Retrieval Pipeline

AMO V2 retrieval answers why-code-changed questions from graph truth, not raw chat logs.

## Pipeline

```text
query
-> query classifier
-> exact + BM25 + vector candidates
-> deterministic fusion
-> graph neighborhood expansion for top candidates
-> cross-encoder rerank top candidates when configured as a secondary signal
-> answer with packet/commit/evidence/code citations
```

## Ranking Rules

The ranker is graph-first. Vector and cross-encoder scores improve ordering, but
they do not replace graph provenance or packet-level reasoning.

- `code_why` queries prefer answer-grade reasoning nodes, then linked code and symbols.
- `decision_history` queries prefer primary reasoning text over changed-path metadata.
- Hook queries expand to the AMO hook behavior vocabulary: capture, injection, prompt, and `UserPromptSubmit`.
- Agent names such as Codex/Claude are treated as context for hook queries, not the whole topic.
- Supporting evidence, commit hubs, and test artifacts are penalized unless the query is actually about those artifacts.
- Cross-encoder reranking remains enabled, but for `decision_history` it uses a smaller weight because local code-oriented rerankers can over-score the literal word "decision" and under-score final policy nodes.

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

## Real V2 Reset Validation

The current real-session validation target is:

```text
.tmp/reasoning-graph-v2-reset-2026-05-14/
```

Run:

```powershell
python .tmp\reasoning-graph-v2-reset-2026-05-14\07_retrieval_pipeline\stage7d_vector_query_eval.py
```

Expected high-level result:

- `stage_acceptance = PASS`
- `vector_status = faiss:completed`
- `reranker = deterministic+bi_encoder+cross_encoder`
- `what decisions were made about Codex hooks?` ranks `Fix: Hook behavior change to capture-only` first.
- `which code changes are connected to Qwen extraction?` ranks Stage 4 decision-extraction reasoning first.
- `why did we add focused evidence windows?` ranks the focused evidence window reasoning and code nodes.
