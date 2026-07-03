# Algorithm: Relationship Explorer

## Purpose

Find meaningful relationships among multiple files, symbols, classes, commits,
or code regions.

## Inputs

- goal
- search terms
- multiple anchors
- constraints
- budget
- graph with structural and semantic evidence

## Algorithm Family

```text
AMO-REL Relationship Explorer
```

## Algorithm Stages

```text
1. Resolve anchors.
2. Assign node prizes from relevance, currentness, role, and semantic evidence.
3. Assign edge costs from type, strength, confidence, and source quality.
4. Run weighted bounded expansion.
5. Run PPR/RWR-style proximity scoring.
6. Use a Steiner-style compact connector for multi-anchor linkage.
7. Rank paths by relevance, coherence, source quality, and active-version support.
8. Run gap-driven second expansion when anchors remain weakly connected.
9. Compress paths with MMR-style diversity.
```

## Readiness Levels

```text
v1 structural: imports, calls, contains, versions, tests
v2 semantic: reviewed RelationOccurrence reasons and ReasoningFrames
v3 enriched: semantic embeddings and source-aware history ranking
```

## Output

```json
{
  "relationship_paths": [],
  "missing_or_weak_links": [],
  "confidence": 0.0
}
```

## Rules

- Do not run full graph walks.
- Do not treat co-change as semantic causality without reviewed reasons.
- Return `partial_structural` when relation reasons are absent.
- Keep output path-based and evidence-grounded.
