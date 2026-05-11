# Edge Types

## Depends on
- node-types.md
- provenance-and-evidence.md

## Used by
- central-versioning-rules.md
- ../algorithms/relationship-extraction.md
- ../algorithms/leiden-community-detection.md

## Related docs
- ../modules/central-graph-merge-engine.md
- ../algorithms/dependency-propagation.md

## Required Base Fields

Every edge must have:

```json
{
  "id": "stable edge id",
  "source_id": "source node id",
  "target_id": "target node id",
  "kind": "edge kind",
  "weight": 1.0,
  "confidence": 0.0,
  "evidence_ids": ["raw ids"],
  "extraction_run_id": "run id when derived",
  "commit_id": "commit when code-linked",
  "created_at": "ISO timestamp",
  "source": "rule|qwen|git|system",
  "metadata": {}
}
```

## Timeline Edges

`PART_OF`: event belongs to session or app/project.

`FOLLOWED_BY`: ordered event chain within a session.

`MENTIONS_FILE`: timeline event mentions or touches a file.

## Provenance Edges

`EVIDENCED_BY`: derived node points to raw evidence.

`CLEANED_INTO`: raw/timeline evidence was included in a cleaned window.

`EXTRACTED_AS`: cleaned window produced extraction output.

`CREATED_BY_RUN`: node or edge was created by an `ExtractionRun`.

## Session Reasoning Edges

`HAS_THREAD`: session has decision thread.

`CONTINUES_TOPIC_OF`: later chunk continues earlier topic.

`CAUSED_BY`: decision reason depends on another decision/cause.

`REFINES`: new decision adds detail without replacing old one.

`SUPERSEDED_BY`: old node was replaced by newer node.

`REVERTS`: new decision/code restores or undoes older change.

`CONFLICTS_WITH`: two active claims are incompatible.

`DEPENDS_ON`: one decision assumes another remains valid.

## Code And Validation Edges

`PRODUCED_CHANGE_IN`: decision or fix produced a code node.

`MODIFIES`: code node or decision touches a file.

`LINKED_TO_COMMIT`: code node links to Git commit.

`VALIDATED_BY`: passing test validates work.

`FAILED_VALIDATION`: failing test invalidates or blocks work.

`BLOCKED_BY`: decision/fix blocked by bug/test/error.

## Central Merge Edges

`COMMITTED_AS`: promoted node is committed in Git commit.

`DUPLICATE_OF`: new node repeats existing knowledge.

`INVALIDATES`: superseded decision invalidates downstream dependent claim.

`MERGED_INTO`: session extraction run merged into central graph.

## Community Edges

`MEMBER_OF`: node belongs to community.

`RELATED_TOPIC`: weak support edge for topic grouping.

Community edges must not be treated as proof of correctness.