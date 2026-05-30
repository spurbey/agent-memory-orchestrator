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

Current production implementation starts with a `DecisionFrame` pass and writes
review-state central decision/problem versions. It does not mutate active
decision status.

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

This is deliberately edge-driven. In the production compact graph, the decision text is
often short; the semantic scope comes from packet, commit, evidence, code-node,
symbol, and code-version edges.

For each session decision, fetch central decision/problem versions and persisted
decision-frame ledger rows where at least one is true:

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

Current review behavior is conservative:

- high content overlap plus shared code context may become `DUPLICATE_OF` or
  `REFINES` review candidates and review relation edges,
- explicit replacement language may become a `SUPERSEDES` review candidate,
- incompatible local/remote, enabled/disabled, strict/permissive, or similar
  language may become a `CONFLICTS_WITH` review candidate,
- text-only overlap with no shared file/symbol context becomes
  `RELATED_REVIEW` and is flagged as false-positive risk,
- no candidate mutates active graph truth,
- decision/problem `KnowledgeVersion` nodes are stored with `status=review`.

Future status-transition behavior should follow:

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

The current dry-run was checked against the production job:

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

The current production decision-evolution probe was checked against four AMO
repo jobs after central commit/file memory was stable:

```text
v2job:8fd246b14d2a4470e48cdbac0787c710
v2job:6beac5ad6d8829beb02f0e3995495924
v2job:ee56af8045117801bf07b77de4371d38
v2job:46fa762fa9e3736868f609dec560b845
```

The repo central graph contained ten review decision `KnowledgeVersion` nodes.
Rerunning the planner against the same curated manifests matched those session
frames back to central review versions and produced conservative review
relations. Exact duplicates became `DUPLICATE_OF`; the installer simplification
vs npm publishing pair remained `RELATED_REVIEW` because it shared file context
but represented a different intent. That is the intended behavior: preserve the
version/relation trace, but do not auto-promote a status change.
