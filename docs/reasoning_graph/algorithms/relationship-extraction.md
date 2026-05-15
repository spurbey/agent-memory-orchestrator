# Relationship Extraction

## Depends on
- decision-extraction.md
- code-node-creation.md
- ../modules/qwen-contracts.md

## Used by
- ../modules/session-graph-builder.md
- ../modules/central-graph-merge-engine.md

## Related docs
- validated-by-and-test-linking.md
- decision-deduplication.md
- ../graph_model/edge-types.md

## Inputs

Decision units, code nodes, tests, timeline order, decision thread membership, and optional Qwen relationship classification.

## Outputs

Typed edges with confidence, evidence ids, source, and extraction run id.

## Relationship Rules

`CAUSED_BY`: explicit because-clause maps to another decision, bug, dependency, or tool-confirmed cause. Qwen can classify ambiguous cause mapping.

`PRODUCED_CHANGE_IN`: decision occurs before write/code node in same thread and shares file/entity.

`REFINES`: new decision adds specificity without invalidating old decision.

`SUPERSEDED_BY`: new decision replaces old decision on same subject.

`REVERTS`: revert signal plus same code node family or same decision subject.

`CONFLICTS_WITH`: same subject/predicate but incompatible object/value and no proof that one supersedes the other.

## Pairing Limits

Do not compare every decision pair globally. Pair within the same `DecisionThread` first, then compare against central candidates during merge.

## Qwen Use

Qwen is allowed only for ambiguous `CAUSED_BY` and relationship classification. It must return schema-valid relation, confidence, and reason. Confidence below `0.70` becomes review candidate.

## Tests

- Because phrase creates `CAUSED_BY`.
- Decision before code write creates `PRODUCED_CHANGE_IN`.
- Revert phrase plus same code node creates `REVERTS`.
- Conflicting values create `CONFLICTS_WITH` or review candidate.
