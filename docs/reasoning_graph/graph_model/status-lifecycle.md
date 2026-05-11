# Status Lifecycle

## Depends on
- node-types.md
- edge-types.md

## Used by
- central-versioning-rules.md
- ../modules/central-graph-merge-engine.md
- ../algorithms/dependency-propagation.md

## Related docs
- extraction-run-versioning.md
- provenance-and-evidence.md

## Statuses

`draft`: raw or derived graph work not finalized.

`session_final`: selected extraction run output for one session.

`active`: current central knowledge that has not been superseded or contested.

`committed`: central knowledge linked to a Git commit.

`refined`: older knowledge still valid but superseded in specificity by a newer node.

`superseded`: older knowledge replaced by newer knowledge.

`contested`: incompatible claims exist and neither is proven dominant.

`contested_pending_review`: dependency propagation found downstream knowledge that may be invalid.

`abandoned`: work was intentionally dropped or no longer pursued.

## Allowed Transitions

```text
draft -> session_final
session_final -> active
session_final -> committed
active -> refined
active -> superseded
active -> contested
committed -> refined
committed -> superseded
committed -> contested
refined -> superseded
superseded -> contested
contested_pending_review -> contested
contested_pending_review -> active
any non-raw status -> abandoned when explicit evidence says abandoned
```

Raw evidence does not transition through answer-grade statuses. Raw evidence remains raw evidence.

## Boundary Handling

A node can be `committed` and still later become `superseded` or `contested`. Commit status means the knowledge was anchored to a Git commit, not that it is forever true.

A low-confidence Qwen output cannot create `active`, `committed`, `refined`, or `superseded` status. It creates a diagnostic or review candidate only.

## Validation Rules

A central answer-grade node must not be `active` or `committed` without evidence ids and extraction run id.

A node marked `superseded` must have at least one outgoing or incoming version edge explaining why.

A node marked `contested_pending_review` must be visible in contested diagnostics.