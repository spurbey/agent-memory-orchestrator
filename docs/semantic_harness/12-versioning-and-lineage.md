# Versioning And Lineage

## Purpose

Versioning lets the harness answer what is current, what changed, and how an entity evolved.

## Version Nodes

- `FileVersion`: file content state at commit or snapshot.
- `SymbolVersion`: symbol state at commit or snapshot.
- `CodeRegionVersion`: code region state at commit or snapshot.

## Lineage Edges

- `CHANGED_IN`: version changed in commit.
- `RENAMED_TO`: same entity renamed.
- `MOVED_TO`: entity moved file or scope.
- `SPLIT_INTO`: one entity split into multiple entities.
- `MERGED_INTO`: multiple entities merged.

## Active Version

The active version is selected by repo branch/view, normally current branch head. Historical versions remain queryable but should not appear as current guidance unless history is requested.

## Rename And Move Policy

Use deterministic signals first:

```text
Git rename metadata
-> same or similar symbol signature
-> body similarity
-> call/import neighborhood similarity
-> review-only if confidence is below threshold
```

## Harness Usage

For `why_changed`, traverse from active version back through commits, work windows, reasoning frames, and relation occurrences. For `edit_plan`, prefer active version and recent high-confidence relation occurrences.
