# Retrieval Pipeline

AMO production retrieval answers why-code-changed questions from graph truth, not raw chat logs.

## Pipeline

```text
query
-> query classifier
-> exact + BM25 + vector candidates
-> deterministic fusion
-> graph neighborhood expansion for top candidates
-> cross-encoder rerank top candidates when configured as a secondary signal
-> typed answer-trace traversal from each top node
-> answer with packet/commit/evidence/code citations
```

## Ranking Rules

The ranker is graph-first. Vector and cross-encoder scores improve ordering, but
they do not replace graph provenance or packet-level reasoning.

- Retrieval is repo-scoped when `repo_id` is provided. The default dashboard
  view can show all repositories for inspection, but answer-grade searches
  should use the selected repository when the user is working inside one repo.
- When `GraphView(main, active)` exists, retrieval projects active central
  `KnowledgeVersion` documents first. Session graph nodes remain indexed as
  packet/commit/evidence/code support.
- Inactive central versions from older graph commits are excluded from the
  active view. Historical/review views must be explicit future modes.
- `code_why` queries prefer answer-grade reasoning nodes, then linked code and symbols.
- `decision_history` queries prefer primary reasoning text over changed-path metadata.
- Hook queries expand to the AMO hook behavior vocabulary: capture, injection, prompt, and `UserPromptSubmit`.
- Agent names such as Codex/Claude are treated as context for hook queries, not the whole topic.
- Supporting evidence, commit hubs, and test artifacts are penalized unless the query is actually about those artifacts.
- Cross-encoder reranking remains enabled, but for `decision_history` it uses a smaller weight because local code-oriented rerankers can over-score the literal word "decision" and under-score final policy nodes.

## Answer Trace Traversal

Retrieval finds the best door into the graph. The answer trace walks from that
door through typed graph edges so the response can explain:

- what problem or goal started the work
- what cause or constraint shaped the change
- what decision was made
- what fix/code change landed
- which packet, commit, evidence, hunk, code node, and symbol support it

Traversal is deterministic. Qwen is not used to walk the graph and cannot invent
links. Qwen can later summarize a finished trace, but the trace itself comes
from stored graph edges.

Current traversal rules:

- Start from each top retrieval hit.
- Walk only answer-grade edge types such as `REASON_NODE_IN_PACKET`,
  `REASON_NODE_EXPLAINS_COMMIT`, `REASON_NODE_EVIDENCED_BY`,
  `REASON_NODE_LINKED_TO_HUNK`, `REASON_NODE_LINKED_TO_CODE_NODE`,
  `COMMIT_PRODUCED_HUNK`, `HUNK_MAPS_TO_CODE_NODE`, and symbol/version edges.
- Use bounded BFS to avoid graph flood.
- Prefer reasoning in the seed packet and seed commit before broader neighbors.
- Prefer visible label/summary query overlap for the answer chain; metadata
  overlap can help retrieval but should not dominate the human-facing narrative.
- Emit at most one chain node per role in the primary answer trace:
  `Problem -> Cause -> Decision -> Constraint -> Fix -> OpenQuestion`.
- Keep extra packets, commits, evidence, hunks, code nodes, and symbols as
  citation support rather than forcing them into the narrative.

## Production Refresh

In production, retrieval refresh is a production job stage, not a separate daemon side
effect:

```text
drain/enqueue closed sessions
-> ProductionSessionJobRunner
-> session graph write
-> central_version_merge
-> retrieval_docs
-> embeddings
-> faiss
```

The production job runner builds retrieval documents only from graph nodes carrying the
current `pipeline_version` and `graph_schema_version`. This keeps any legacy
manual/smoke graph output out of the production retrieval ledger.

Phase 5 retrieval is central-aware:

```text
if active GraphView(repo_id, main, active) has central versions:
  build central-first docs from active KnowledgeVersion/KnowledgeAtom for repo_id
  also index session graph docs as provenance support
else:
  fall back to production session graph docs
```

`GraphCommit` and `GraphView` docs are lineage/debug context. They are not the
primary answer-grade facts. Answer-grade central memory should be a
`KnowledgeVersion` that can trace back to the immutable session graph through
`DERIVED_FROM_SESSION_NODE`, then onward to packet, commit, evidence, hunk, code
node, and symbol support.

If the embedding model is unavailable after graph and retrieval docs are built,
the job pauses as `pending_model`. Graph and lexical retrieval remain available;
vectors and FAISS resume after the model/runtime is restored and the job is
retried.

## Manual Retrieval Index Build

```bash
amo-cli graph-retrieval-build
amo-cli graph-retrieval-embed --model BAAI/bge-m3
```

Scope maintenance to one repository when needed:

```bash
amo-cli graph-retrieval-build --repo-id repo:remote:...
amo-cli graph-retrieval-embed --repo-id repo:remote:... --model BAAI/bge-m3
```

These commands are maintenance/smoke tools. The normal closed-session production
path uses the production job runner so stage status, errors, and retries are observable
from `/api/jobs` and the dashboard Admin view.

## Retrieve

```bash
amo-cli graph-retrieve --query "why did this code change?" --require-vector
amo-cli graph-retrieve --repo-id repo:remote:... --query "why did this code change?"
```

Retrieval has two storage phases:

```text
candidate search -> SQLite FTS / embedding ledger / FAISS
graph expansion -> read-only Kuzu traversal for selected hits
```

`--no-answer --no-vector` is index-only and does not open Kuzu. Graph-expanded
retrieval opens Kuzu read-only. For repo-scoped retrieval, expansion uses the
repo central graph path, not the global `amo.kuzu` graph:

```text
AMO_HOME/.graph/central/<safe_repo_id>/central.kuzu
```

If a direct offline graph-expanded command reports a Kuzu lock, use the daemon
endpoint or stop the incompatible graph owner. Do not retry by opening another
read-write Kuzu handle.

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

## Dashboard Retrieval

The web dashboard Retrieval tab defaults to the same indexed production endpoint as the
CLI: `/graph/retrieve`. It should show:

- generated answer text
- vector status, usually `faiss:completed`
- reranker label, for example `deterministic+bi_encoder+cross_encoder`
- ranked hits
- packet, commit, evidence, code-node, and answer-trace citations

The repository selector at the top of the dashboard sets `repo_id` for sessions,
jobs, central graph, version flow, and retrieval. The Graph Workbench has the
same selector. Use `All repositories` only for operator/debug scans; use a
specific repo for product retrieval so similarly named files or symbols in
different projects do not compete.

The dashboard no longer exposes the older `/graph/search` path. `/graph/search`
remains only as a compatibility/smoke route for old tooling. Product retrieval
must use the production document index, embedding ledger, FAISS cache, reranker, graph
neighborhood expansion, and answer-trace renderer.

Configure the active production source in `config.json`:

```json
{
  "graph_path": ".graph/amo.kuzu",
  "retrieval_db_path": ".data/retrieval.sqlite",
  "retrieval_graph_path": "",
  "retrieval_graph_scope": ""
}
```

`retrieval_graph_path` can point at an isolated production graph when the graph used for
retrieval differs from the dashboard graph. Blank means use `graph_path`.
`retrieval_graph_scope` can be blank; AMO will choose the active embedded scope
for the configured model when possible.

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

## Reset-Fixture Validation

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

Latest trace validation:

- `why did we change graph_service.py?` returns `WP0030` and traces the
  session-graph explanation problem to commit `50b24f6` and graph-service code
  nodes.
- `what decisions were made about Codex hooks?` returns `WP0018` and traces
  hook timeout -> stdin/heavy hook cause -> Kuzu/capture-only decision ->
  capture-only fix.
- `what work was done for Slack connector?` returns `WP0034` and traces Slack
  mention handling -> committed graph answer policy -> threaded reply
  implementation.
- `which code changes are connected to Qwen extraction?` returns `WP0057` and
  traces low decision yield -> deterministic spine plus LLM enrichment policy ->
  strict Qwen decision-quality gate implementation.
- `why did we add focused evidence windows?` returns `WP0046` and traces staged
  module architecture -> validation constraint -> `extraction_window.py` fix.
