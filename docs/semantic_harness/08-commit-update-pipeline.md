# Commit Update Pipeline

## Purpose

After an agent completes work, the harness updates versions, relation evidence, and semantic context from the work window.

## Inputs

- raw hook evidence or AMO evidence refs
- scoped work window
- commit message and SHA
- git hunks
- current repo graph
- tool observations and validation output
- optional Qwen semantic proposals

## Deterministic Stages

```text
1. Resolve work window boundaries.
2. Resolve repo_id and commit_id.
3. Extract zero-context hunks or changed-line ranges and touched files.
4. Parse old and new file snapshots where available.
5. Map hunks to Symbol or CodeRegion with confidence.
6. Create FileVersion, SymbolVersion, and CodeRegionVersion nodes.
7. Update CHANGED_IN and mapping edges.
8. Update structural co-change counts.
9. Attach validation evidence.
10. Refresh retrieval projections.
```

## Semantic Enrichment Stages

```text
1. Build Qwen work-causality packet.
2. Run Qwen proposal extraction.
3. Review proposals deterministically.
4. Create accepted ReasoningFrame nodes.
5. Create RelationOccurrence nodes with reasons.
6. Attach semantic support to cards and traversal.
```

## Qwen Unavailable Mode

If Qwen is unavailable or rejected, deterministic stages still complete. Semantic fields are marked missing or pending. Structural cards remain available.

## Hunk Context Rule

Graph mutation uses changed-line ranges only. The commit-update mapper must not use broad Git context as the hunk span because wide context can overlap unrelated nearby symbols and turn a precise edit into `review_only`.

Broader context is still required, but it belongs in work-window/Qwen packets:

```text
changed-line hunk -> deterministic version and relation update
broad work window -> semantic explanation and Qwen proposal input
```

## Outputs

- updated versions
- hunk mappings
- relation occurrences when accepted
- refreshed cards and projections
- update eval report
