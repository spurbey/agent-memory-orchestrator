# Retrieval, Embeddings, And Projections

## Purpose

Retrieval finds candidates. Graph traversal grounds candidates. Card selection returns compact agent context.

## Default Flow

```text
1. Intent and anchor resolution
2. Radius-1 structural graph lookup for exact anchors
3. BM25/lexical search over names, docs, summaries, reasoning
4. Vector/cosine search when semantic discovery is useful
5. RRF/fusion and optional rerank
6. Deeper typed graph traversal from selected candidates
7. Strict card selection under budget
```

## Graph Step Distinction

Step 2 is cheap direct lookup around exact anchors.

Step 6 is deeper traversal through versions, commits, work windows, relation occurrences, validations, and provenance.

## Vector Use

Use vector search for:

- vague feature names
- semantic task matching
- similar prior work
- relation occurrence summaries
- symbol/file summaries
- historical reasoning

Skip or downweight vector search for:

- file path
- qualified symbol
- commit SHA
- test name
- stack trace frame
- line reference

## Embedding Sources

Embed high-signal summaries:

- file summaries
- symbol summaries
- code-region summaries
- work-causality frames
- relation occurrence summaries
- harness cards
- selected docs and docstrings

Do not embed raw AST flood as product retrieval memory.

## Projection Document Contract

The first projection layer is deterministic and storage-neutral. It emits high-signal records from graph truth:

```json
{
  "doc_id": "pdoc:<repo_id>:<hash>",
  "repo_id": "repo:...",
  "source_node_id": "symbol:...",
  "source_kind": "File|Symbol|DocSection|DocString",
  "doc_type": "file_summary|symbol_summary|doc_semantic_summary",
  "title": "short indexed title",
  "text": "compact indexed body",
  "content_hash": "sha256",
  "metadata": {
    "path": "src/...",
    "status": "active",
    "projection_source": "semantic_harness_graph"
  }
}
```

Projection sets wrap these documents with cache identity:

```json
{
  "projection_id": "hproj:<hash>",
  "projection_version": "semantic_harness_projection_v1",
  "graph_snapshot_id": "gsnap:<repo_id>:<hash>",
  "graph_schema_version": "semantic_harness_graph_v1",
  "document_count": 42,
  "document_ids_hash": "sha256"
}
```

`graph_snapshot_id` remains structural. `projection_id` is derived from
`projection_version + graph_snapshot_id + rendered projection content hash`.
This keeps structural graph identity stable while still invalidating
projection/vector caches when semantic facts, summaries, or doc claims change
without adding/removing nodes or edges.

Document content hashes remain per-document integrity checks and drive
embedding reuse:

```text
same doc_id + same content_hash -> reuse embedding
same doc_id + changed content_hash -> re-embed
missing doc_id in active projection -> tombstone/remove from active index
```

Default projected node kinds:

```text
File
Symbol
DocSection
DocString
```

Do not project these in the bootstrap slice:

```text
FileVersion
SymbolVersion
CodeRegionVersion
Hunk
RelationOccurrence
raw AST/debug nodes
```

Those may get specialized projections later, after evals prove they are useful and not noisy.

Projection text must be graph-grounded:

```text
file_summary:
  path
  language
  file summary
  defined symbol names
  module/file docstring summaries

symbol_summary:
  qualified name
  symbol kind
  path
  signature/source summary
  attached symbol docstring summaries
  directly called symbol names
  direct caller symbol names

doc_semantic_summary:
  doc section/docstring title
  path
  doc kind
  documented target when parser-backed
  compact content excerpt

semantic_fact_summary / relationship_fact_summary / doc_claim_summary / work_window_fact_summary:
  one reviewed semantic fact per projection document
  fact type, scope, derivability, source kind, verification status, trust tier
  accepted fact text
  source refs and anchor node ids in metadata
```

Projection docs must not invent dependencies. If a cross-file call is not structurally resolved, the projection may include the imported file via `IMPORTS`, but it must not claim a `CALLS` relation.

Normal retrieval projects only accepted semantic facts. `review_only` facts are
audit/debug opt-in. Rejected and quarantined facts are not projected for normal
retrieval.

Cross-file `CALLS` are projection-safe only when they come from deterministic structural resolution. The first Python resolver supports local `from module import symbol` calls and aliased module calls such as `import package.module as mod; mod.symbol()`. It skips wildcard imports, dynamic attributes, third-party imports, class method attributes reached through a module alias, and dotted imports without an alias when the local name would be ambiguous.

## Doc Support Retrieval

For exact file or symbol anchors, direct graph lookup should inspect deterministic doc edges before fuzzy retrieval:

```text
DocString DOCUMENTS_SYMBOL anchor
DocString DOCUMENTS_FILE anchor
DocSection MENTIONS_SYMBOL anchor
DocSection MENTIONS_FILE anchor
```

These produce compact `doc_support` cards. They do not require vector search. Vector search may later discover candidate doc sections for vague feature names, but those candidates must be grounded to file/symbol graph evidence before they become cards.

## Candidate Trust

Vector candidates are not truth. If a vector hit cannot be grounded to typed graph evidence, return `low_confidence` or omit the card.

## Lexical Retrieval MVP

The first retrieval implementation is BM25-style lexical search over projection documents. It is intentionally storage-neutral and deterministic.

Input:

```text
query text from user_goal, anchors, errors, and recent tool result
projection documents generated from graph truth
```

Output:

```text
ranked LexicalRetrievalHit records
matched terms
raw score
normalized score
source_node_id
```

Rules:

```text
exact anchor cards are selected before lexical candidates
lexical hits must ground through source_node_id to an existing graph node
ungrounded hits are dropped
lexical cards are candidate-discovery cards, not final truth
vague goals with grounded lexical hits return partial_structural in the structural-only phase
```

The scorer tokenizes identifiers, paths, docs, and summaries, applies BM25-style term weighting, and gives small deterministic boosts to high-signal document classes such as `symbol_summary`. It does not use vectors, LLMs, or graph mutation.

For unanchored `edit_plan`, `tool_overlay`, and `impact_check` requests, the
first lexical pass aggregates weak symbol/doc hits by source file before cards
are selected. This is deliberate. A coding agent usually needs "open this file
next" before it needs a low-confidence symbol guess.

Aggregation rules:

```text
collect lexical hits from graph-grounded projection docs
map Symbol/DocString/File hits back to their owning File node
exclude tests, docs, markdown, and text files from default edit-planning cards
sum weak evidence by source file
boost files whose path tokens overlap query terms
return next_file cards with retrieval_source=lexical_file_aggregate
skip vector fallback once lexical file aggregate cards exist
```

This prevents unanchored edit planning from returning vector-only tangents when
several weak lexical hits point to the same relevant implementation file.

## Vector Retrieval MVP

The first vector implementation is deterministic hash-cosine over projection documents. It is not a model-quality replacement for learned embeddings; it is a local candidate source for smoke-tested semantic and identifier-variant discovery.

Embedding method:

```text
hash_token_char_cosine_v1
```

Features:

```text
token features from normalized projection text
character n-gram features for identifier variants
compact identifier aliases such as sign_in_user -> signin and signinuser
```

Query behavior:

```text
exact anchors remain first
lexical candidates are considered before vector candidates
vector runs when anchors are absent/incomplete and lexical did not already produce grounded cards
vector does not rescue a request where every explicit file/symbol anchor failed to resolve
vector hits must ground through source_node_id to an existing graph node
vector cards expose retrieval_source=vector and embedding_method
```

Vector cards remain conservative:

```text
status remains partial_structural in the structural-only phase
confidence is capped below exact/lexical grounded cards
unmatched vector queries return unavailable
```

## Budget And Novelty Selection MVP

All candidate routes feed one card selector before the agent sees output:

```text
exact file/symbol anchors
doc_support cards
dependency cards
historical_relation cards
lexical projection cards
vector projection cards
```

The selector ranks candidates with a conservative weighted score:

```text
route priority: 0.45
card type priority: 0.25
card confidence: 0.22
evidence density: 0.08
```

Route priority is deliberately asymmetric:

```text
exact anchor card: 1.00
doc_support: 0.82
historical_relation: 0.72
dependency: 0.68
lexical projection: 0.50
vector projection: 0.38
fallback structural card: 0.60
```

This means vector search can discover candidates, but it cannot displace exact file/symbol anchors or deterministic graph support unless the exact routes have no remaining budget-worthy card.

The selector suppresses:

```text
already_seen_card
already_seen_nodes
duplicate_selected_nodes
max_cards
max_tokens
```

Suppressed reasons are retained for eval/debug. The normal response only returns selected cards, keeping agent-facing context compact.

## Projection Health

Projection metadata must record source graph version, embedding model, document content hash, vector count, and readiness status.

## Service-Level Projection Cache

The first cache is application-local and rebuildable:

```text
StructuralHarnessService
-> InMemoryProjectionCache
-> HarnessProjectionSet
```

Cache key:

```text
projection_id = hash(projection_version + graph_snapshot_id)
```

Rules:

```text
exact-anchor queries do not build projection docs when the card budget is already filled
lexical/vector routes request projection docs lazily
same graph_snapshot_id + projection_version reuses the in-memory projection set
new structural graph shape creates a new graph_snapshot_id and projection_id
```

This cache is not durable truth. The future SQLite projection store should persist the same `projection_id`, `graph_snapshot_id`, `projection_version`, `document_ids_hash`, and document rows.
