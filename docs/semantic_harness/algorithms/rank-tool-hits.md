# Algorithm: Rank Tool Hits

## Purpose

Rank broad search output so the coding agent opens the most relevant files and
symbols first. This mode is rank-only and does not return generic cards.

## Inputs

- goal
- search terms
- recent tool result from `rg`, `grep`, or equivalent search
- latest captured `UserPromptSubmit` text for the session when available
- already-seen state
- current structural graph
- projection documents for candidate files, symbols, code regions, semantic
  facts, relation occurrences, docs, and work-window summaries
- embedding index for projection documents, or an explicit degraded fallback

## Algorithm

```text
1. Parse tool rows into file, line, and text hits.
2. Normalize paths and group hits by file.
3. Map each line to File, Symbol, or CodeRegion when possible.
4. Build candidate-local evidence bundles for files returned by the tool.
5. Run candidate-local semantic similarity:
   query text = current user goal + latest UserPromptSubmit + search terms
   documents = projection docs attached to the candidate files/symbols/edges
6. Score each group with explicit feature weights.
7. Suppress generated/vendor/noise hits.
8. Diversify top groups so one directory or repeated symbol cannot dominate.
9. Return ranked groups with reason codes.
```

## Score Inputs

```text
lexical overlap with goal/search_terms
line-to-symbol confidence
path role: source, test, docs, config
active-version status
structural proximity to known anchors
candidate-local semantic similarity
historical/co-change signal with minimum occurrence gate
validation/test relevance
already-seen penalty
noise penalty
```

## Candidate-Local Similarity

Similarity search is allowed only inside the candidate set created by the tool
output. `rg` or equivalent search first defines the candidate files and line
ranges. AMO then gathers projection documents attached to those candidate graph
objects:

```text
File summary docs
Symbol summary docs
CodeRegion summary docs
accepted semantic fact docs
verified doc/docstring claim docs
RelationOccurrence summary docs
WorkWindow summary docs
selected validation/test docs
```

The query vector is built from:

```text
latest captured UserPromptSubmit text
current agent goal
explicit search_terms
optional active anchors
```

This similarity score helps choose among `rg` results. It is not global
semantic search and it is not graph truth.

Rules:

```text
actual embedding backend preferred
hash_token_char_cosine_v1 allowed only as degraded fallback
degraded fallback must be reported in the result
no raw AST flood embedding
no vector-only answer claims
no candidate outside raw tool output unless explicitly requested
```

## Scoring Formula V1

Default anchored scoring:

```text
score =
  0.20 * rg_match_strength
+ 0.20 * line_symbol_grounding
+ 0.20 * graph_proximity_to_anchors
+ 0.15 * candidate_semantic_similarity
+ 0.10 * historical_signal
+ 0.10 * path_role_prior
+ 0.05 * validation_or_test_relevance
- penalties
```

When no strong anchors exist, semantic similarity may rise but must stay bounded:

```text
score =
  0.20 * rg_match_strength
+ 0.15 * line_symbol_grounding
+ 0.10 * graph_proximity_to_anchors
+ 0.30 * candidate_semantic_similarity
+ 0.10 * historical_signal
+ 0.10 * path_role_prior
+ 0.05 * validation_or_test_relevance
- penalties
```

Penalties:

```text
already_seen_file: -0.10 to -0.20
generated/vendor/build/cache path: -0.30
unresolved graph node: -0.15
weak one-off co-change only: no boost
too many generic matches in file: -0.05 to -0.15
```

Historical signal must respect the co-change minimum occurrence gate. A high
Jaccard score from one or two observations is not enough to rank as a strong
historical relation.

## Output

```json
{
  "embedding_backend": "actual_model|hash_fallback|disabled",
  "semantic_similarity_used": true,
  "ranked_groups": [
    {
      "rank": 1,
      "file": "src/example.py",
      "best_lines": [42],
      "mapped_symbols": ["Example.symbol"],
      "score": 0.91,
      "reason_codes": [
        "strong_rg_match",
        "line_grounded",
        "symbol_match",
        "semantic_doc_match",
        "source_file"
      ],
      "raw_ref": "sha256:..."
    }
  ],
  "suppressed": []
}
```

## Phase Gate

The mode must beat raw `rg` baseline on real-session replays:

- fewer irrelevant file opens
- faster time to correct file
- eventual edited file appears in top three
- no hot-path bootstrap
- no Qwen dependency
- actual embedding backend path tested at least once
- hash fallback marked as degraded when used
- semantic similarity changes at least one ranking on a broad real `rg` fixture
- raw tool output remains recoverable by `raw_ref`
