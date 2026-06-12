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
