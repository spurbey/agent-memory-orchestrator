# Entity Resolution

## Depends on
- ../graph_model/node-types.md
- ../modules/embeddings-runtime.md

## Used by
- decision-deduplication.md
- ../modules/central-graph-merge-engine.md
- ../implementation/05-phase-central-merge.md

## Related docs
- dependency-propagation.md
- ../graph_model/central-versioning-rules.md

## Purpose

Before central merge, determine whether a session graph entity matches an existing central graph entity.

## Candidate Query

Fetch candidates by compatible kind, shared normalized name tokens, shared file path, shared subject, or nearby embedding result. Do not compare against raw/timeline/support-only nodes unless resolving support entities.

## Score Components

`string_similarity`: normalized Levenshtein similarity in range `0.0-1.0`.

`embedding_similarity`: cosine similarity between BGE-M3 embeddings of entity names/summaries.

`structural_jaccard`: direct neighbor id Jaccard:

```text
|neighbors(a) intersect neighbors(b)| / |neighbors(a) union neighbors(b)|
```

If both neighbor sets are empty, structural score is `0.0`.

## Formula

```text
score = 0.50 * string_similarity + 0.30 * embedding_similarity + 0.20 * structural_jaccard
```

## Boundaries

Score `>= 0.85`: same entity.

Score `>= 0.65` and `< 0.85`: review candidate or Qwen-assisted classification.

Score `< 0.65`: different entity.

Exactly `0.85` is same entity. Exactly `0.65` is review candidate.

## Tests

- Same normalized file/entity names resolve.
- Similar names with different neighbors enter review.
- Different names and structure create new entity.
- Missing embedding records diagnostic and lowers confidence.