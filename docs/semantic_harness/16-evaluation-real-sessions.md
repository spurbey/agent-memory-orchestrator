# Evaluation On Real Sessions

## Purpose

Evaluate whether the harness improves actual coding-agent behavior over raw tools and current AMO retrieval.

## Baselines

Each eval compares:

```text
raw rg/open baseline
current AMO retrieval baseline
semantic harness cards
```

## Metrics

```text
strict_card_precision >= 0.85
next_file_hit_rate_top3 >= 0.80
test_selection_hit_rate >= 0.75
idempotent_replay_rate = 1.0
mislead_rate <= 0.05 before sidecar auto-injection
```

## Required Fixtures

Rich-history fixture:

```text
at least 3 commits
accepted reasoning exists
code/file/symbol support exists
answer trace reaches packet, commit, file, and code evidence
at least one query returns status=ready
at least one historical relation occurrence is useful to the agent
```

Selected rich-history production fixture:

```text
fixture_id = rich-amo-peer-context-b387ce0f
session_dir = 019e5093-2b25-7d21-8238-2c94614a19da-b387ce0f
job_id = v2job:b387ce0faad2faf4885bd1267106071b
repo_id = repo:remote:311ebb9cda1fb40f
curated_graph_manifest = 230 nodes, 292 edges
packets = 20
accepted_reasoning_nodes = 16
central_merge = applied
active_projection_id = rproj:4bd1501186f83d89d8ada22ed7e4bfc0
retrieval_docs = 6980
faiss_items = 6980
```

Primary ready query:

```text
query = peer answer prompt deterministic context shape
expected_status = ready
expected_top_support = FileImpactSummary service_utils.py; CodeImpactSummary WP0003; Packet WP0003; answer-grade ReasoningNode for commit 4f480a4
expected_agent_action = inspect service_utils.py first and keep the edit scoped to prompt compaction and responder timeout capping
```

Partial-history fixture:

```text
structural graph exists for relevant files
semantic reasoning is missing, weak, or review-only
co-change or structural dependency evidence exists
expected output is partial_structural, partial_historical, partial_coverage, low_confidence, or unavailable
```

Selected partial-history production fixture:

```text
fixture_id = partial-amo-strict-locator-3c901ff2
session_dir = 019e76e1-98dd-75d1-8e8f-046679339444-3c901ff2
job_id = v2job:3c901ff20e08a147109af56a301c9207
repo_id = repo:remote:311ebb9cda1fb40f
curated_graph_manifest = 26 nodes, 32 edges
packets = 2
accepted_reasoning_nodes = 1
central_merge = applied
active_projection_id = rproj:fb0be7e05ded6c4920d972002e92030e
retrieval_docs = 6521
faiss_items = 6521
```

Primary partial query:

```text
query = strict code locator ranking retrieval
expected_status = partial_historical
expected_top_support = FileImpactSummary ranking.py; FileImpactSummary query.py; SymbolRef _strict_code_locator_match; SymbolRef _strict_code_locator_terms; CodeRegionRef _strict_code_locator_match
expected_agent_action = inspect ranking.py first, then query.py if the behavior is not contained in strict locator ranking
```

## Required Scenarios

- rich history returns ready cards
- structural-only repo returns partial_structural
- weak semantic memory returns partial_historical
- mixed anchor coverage returns partial_coverage
- weak vector-only candidate returns low_confidence
- no trusted graph context returns unavailable
- exact file query skips or downweights vector
- vague semantic query uses vector then graph traversal
- Qwen unavailable still updates structural graph
- bad Qwen frame is quarantined and does not affect cards
- card feedback records shown, acted_on, ignored, or invalidated

## Historical Relation Card Gate

`historical_relation` evals must check both strength and evidence count:

```text
stored_strength >= 0.40
cochange_count >= 3
```

Required negative case:

```text
two perfect co-changes with Jaccard 1.0
-> stored_strength = 0.45
-> cochange_count = 2
-> no historical_relation card
```

Required positive case:

```text
three perfect co-changes with Jaccard 1.0
-> stored_strength = 0.45
-> cochange_count = 3
-> historical_relation card may be shown
```

If an eval lowers `min_cochange_count`, the report must include the non-default threshold. Hidden threshold changes invalidate the eval.

Occurrence relevance checks:

```text
task-matching commit/reason occurrence
-> cited before structural_fallback occurrences

strict relevance mode + no task-matching occurrence
-> no historical_relation card
```

Each cited occurrence must expose `task_relevance` and `matched_terms` so false-positive review can tell whether the card was grounded in task text or only in aggregate structural history.

Doc support checks:

```text
Markdown section with exact repo-relative file path
-> DocSection MENTIONS_FILE File

Python symbol docstring
-> DocString DOCUMENTS_SYMBOL Symbol

Exact symbol query with docstring
-> doc_support card cites DocString and DOCUMENTS_SYMBOL edge
```

These checks must pass without embeddings or LLM calls. Fuzzy doc discovery is evaluated separately after vector projections exist.

Projection document checks:

```text
bootstrap graph with File, Symbol, DocSection, DocString
-> projection docs include file_summary, symbol_summary, doc_semantic_summary

projection source kinds
-> only File, Symbol, DocSection, DocString in bootstrap slice

projection content
-> includes exact path, qualified symbol name, docstring text, doc section excerpt, defined symbols, and direct call neighbors

forbidden bootstrap projection
-> no FileVersion, SymbolVersion, Hunk, RelationOccurrence, or raw AST/debug node docs
```

## Concrete Replay Cases

| Case | Fixture | Query | Expected Status | Must Pass |
| --- | --- | --- | --- | --- |
| ready-peer-context | rich-amo-peer-context-b387ce0f | `peer answer prompt deterministic context shape` | `ready` | top cards cite packet `WP0003`, commit `4f480a4`, `service_utils.py`, and answer-grade reasoning |
| partial-strict-locator | partial-amo-strict-locator-3c901ff2 | `strict code locator ranking retrieval` | `partial_historical` | cards cite `ranking.py`, `_strict_code_locator_match`, `_strict_code_locator_terms`, commit `30ba86e`, and warn that history is narrow |
| exact-anchor-vector-downweight | partial-amo-strict-locator-3c901ff2 | `src/agent_memory_orchestrator/domain/retrieval/ranking.py::_strict_code_locator_match` | `partial_historical` | exact symbol/path grounding ranks before vector-only candidates |
| vague-peer-semantics-vector-grounded | rich-amo-peer-context-b387ce0f | `peer prompt keeps timing out because context is huge` | `ready` or `low_confidence` | vector candidates must ground to FileImpactSummary, CodeImpactSummary, Packet, or ReasoningNode before any card is returned |

## Failure Interpretation

- If a returned card has only vector evidence, the case fails even if the text sounds relevant.
- If a partial fixture returns `ready` without enough accepted semantic history, the case fails because the harness is overclaiming.
- If the rich fixture returns raw trace nodes instead of curated support docs, the case fails because product retrieval leaked debug memory.
- If repeated replay changes card IDs, support node IDs, or status without graph input changes, the case fails idempotency.

## Eval Report Shape

```json
{
  "case_id": "string",
  "fixture": "string",
  "query": {},
  "expected": {},
  "actual": {},
  "passed": true,
  "metrics": {},
  "failure_reason": ""
}
```
