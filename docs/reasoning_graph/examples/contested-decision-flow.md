# Example: Contested Decision Flow

## Depends on
- ../algorithms/dependency-propagation.md
- ../graph_model/status-lifecycle.md

## Used by

## Related docs
- ../modules/web-debug-visibility.md
- ../graph_model/central-versioning-rules.md

## Scenario

Central graph has decision D1: `NDK 27 is required for Sentry`. Another active decision D2 depends on D1: `Mapbox build works because NDK 27 ABI is available`.

A new session introduces D3: `Upgrade NDK to 28 because new Sentry supports it`.

## Merge Result

If D3 supersedes D1:

```text
D3 SUPERSEDES D1
D1 status -> superseded
```

## Dependency Propagation

BFS follows incoming `DEPENDS_ON` edges to D2.

```text
D2 DEPENDS_ON D1
D3 INVALIDATES D2
D2 status -> contested_pending_review
```

## Surfacing

Startup briefing and web contested panel must show D2 as needing review, with source D3, evidence refs, and commit ids.
