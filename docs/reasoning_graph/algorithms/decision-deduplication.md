# Decision Deduplication

## Depends on
- entity-resolution.md
- ../graph_model/central-versioning-rules.md

## Used by
- ../modules/central-graph-merge-engine.md
- ../implementation/05-phase-central-merge.md

## Related docs
- relationship-extraction.md
- dependency-propagation.md

## Candidate Query

For each session decision, fetch central decisions where at least one is true:

- same resolved subject entity,
- same file path or code node,
- same topic/community candidate,
- lexical overlap on normalized subject/predicate/object,
- embedding nearest neighbor above broad threshold `0.50`.

## Score Components

`cosine`: BGE-M3 cosine between decision summaries or structured subject/predicate/object text.

`lexical`: token Jaccard after lowercase, stopword removal, and stemming-like suffix normalization.

`entity_jaccard`: Jaccard of resolved entity ids mentioned by both decisions.

`same_topic`: `1.0` if same decision thread or community candidate, `0.5` if related topic, `0.0` otherwise.

## Formula

```text
relatedness = 0.45 * cosine + 0.25 * lexical + 0.20 * entity_jaccard + 0.10 * same_topic
```

## Classification

`>= 0.85` and same subject/predicate/object: `DUPLICATE_OF`.

`>= 0.75` and same subject/predicate but more specific object: `REFINES`.

`>= 0.65` and same subject/predicate but incompatible object: classify as `SUPERSEDES` or `CONFLICTS_WITH` using evidence.

`< 0.65`: new decision.

## Tests

- Exact same decision dedupes.
- Specific version refines broad family decision.
- Conflicting values produce conflict or supersede candidate.
- Different topic remains new.