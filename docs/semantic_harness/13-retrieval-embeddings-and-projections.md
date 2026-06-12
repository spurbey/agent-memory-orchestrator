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
```

Projection docs must not invent dependencies. If a cross-file call is not structurally resolved, the projection may include the imported file via `IMPORTS`, but it must not claim a `CALLS` relation.

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

## Projection Health

Projection metadata must record source graph version, embedding model, document content hash, vector count, and readiness status.
