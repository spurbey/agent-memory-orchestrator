# Central Graph Merge Engine

## Depends on
- extraction-run-manager.md
- ../graph_model/central-versioning-rules.md
- ../algorithms/entity-resolution.md
- ../algorithms/decision-deduplication.md

## Used by

## Related docs
- qwen-contracts.md
- ../algorithms/dependency-propagation.md
- graph-validation.md

## Purpose

Promote selected session graph nodes into central graph and preserve version history.

## Inputs

Selected extraction run, commit details, diff summary, patch id, session graph
nodes, `repo_id`, and existing central candidates for the same repository.

## Outputs

Central nodes, merge/version edges, status updates, review candidates,
contested propagation, `GraphCommit`, and repo-scoped `GraphView`.

## Owned state

Merge plan, merge result records, central merge locks, graph commits, graph
views, and review candidates in SQLite. Kuzu stores graph-visible central nodes
and lineage.

## Public interfaces planned

- `plan_merge(session_id, extraction_run_id, commit) -> MergePlan`
- `apply_merge(plan_id) -> MergeResult`

Production V2 currently exposes this through `central_version_merge` job
artifacts plus:

```text
amo-cli v2-merge-plan --job-id ...
amo-cli v2-merge-apply --plan-id ...
amo-cli graph-version-flow --repo-id ...
```

## Kuzu writes

The exact-atom implementation writes `KnowledgeAtom`, `KnowledgeVersion`,
`GraphCommit`, `GraphView`, `VERSION_OF`, `DERIVED_FROM_SESSION_NODE`,
`TOUCHES_FILE`, `TOUCHES_SYMBOL`, and `IMPLEMENTED_BY_COMMIT`.

Every central write carries `repo_id`, `merge_plan_id`, `graph_commit_id`,
`pipeline_version`, `graph_schema_version`, and an idempotency key. Locks and
GraphView heads are scoped by `repo_id + branch + mode`, so two repositories do
not block or overwrite each other.

Decision/problem evolution edges (`DUPLICATE_OF`, `REFINES`, `SUPERSEDES`,
`CONFLICTS_WITH`, `REVERTS`) are intentionally deferred until semantic evals
prove safe matching. They should start as review candidates, not automatic
truth.

## Failure modes

Missing `repo_id` blocks answer-grade central promotion because canonical keys
would be ambiguous. Branch-head mismatch for the same `repo_id` forces replan.
Low-confidence relation becomes review candidate. Missing commit blocks
code-linked central promotion unless explicit non-code finalize is requested.
Invalid provenance rejects node promotion.

## Validation checks

No raw/support node promoted. Every promoted node has evidence, extraction run,
`repo_id`, and commit when code-linked. Retrieval should be able to ask for one
repo and see only that repo's active central view plus session provenance.
