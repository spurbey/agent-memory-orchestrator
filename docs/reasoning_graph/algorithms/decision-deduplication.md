# Decision Deduplication

## Depends on
- entity-resolution.md
- ../graph_model/central-versioning-rules.md

## Used by
- ../modules/central-graph-merge-engine.md

## Related docs
- relationship-extraction.md
- dependency-propagation.md

## Candidate Query

Current production implementation starts with a dry-run `DecisionFrame` pass.
It does not create central decision atoms and does not mutate decision status.

For every accepted session `ReasoningNode` with decision/problem semantics,
the merge planner builds:

- `repo_id`,
- source reasoning node id,
- summary, subject, statement, and rationale,
- linked files,
- linked symbols,
- linked code nodes and code versions,
- linked commits,
- linked packets,
- evidence refs,
- graph-neighbor signature from typed `REASON_NODE_*` edges.

This is deliberately edge-driven. In the V2 compact graph, the decision text is
often short; the semantic scope comes from packet, commit, evidence, code-node,
symbol, and code-version edges.

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

Current dry-run behavior is conservative:

- high content overlap plus shared code context may become `DUPLICATE_OF` or
  `REFINES` review candidates,
- text-only overlap with no shared file/symbol context becomes
  `RELATED_REVIEW` and is flagged as false-positive risk,
- no candidate mutates graph truth,
- decisions/problems remain deferred central atoms until semantic evals prove
  the candidate relation is safe.

Future apply behavior should follow:

`>= 0.85` and same subject/predicate/object: `DUPLICATE_OF`.

`>= 0.75` and same subject/predicate but more specific object: `REFINES`.

`>= 0.65` and same subject/predicate but incompatible object: classify as `SUPERSEDES` or `CONFLICTS_WITH` using evidence.

`< 0.65`: new decision.

## Tests

- Exact same decision dedupes.
- Specific version refines broad family decision.
- Conflicting values produce conflict or supersede candidate.
- Different topic remains new.

## Real Production Probe

The current dry-run was checked against the V2 job:

```text
v2job:1fc7fea2efc46cb1cff9b01ebedc4319
```

The production compact graph produced:

```text
decision frames: 7
review candidates: 1
relation: RELATED_REVIEW
reason: text_overlap_without_shared_code_context
false_positive_risk: true
```

The candidate linked two "server debug runbook/current behavior baseline"
decisions, but their file/symbol contexts were different. That is correct for
the current phase: surface it for review, but do not merge or change status.
