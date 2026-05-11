# Phase 8: Web Debugging

## Depends on
- ../modules/web-debug-visibility.md
- 07-phase-fresh-rebuild.md

## Used by
- 09-test-and-acceptance-gates.md

## Related docs
- ../examples/code-query-flow.md
- ../examples/contested-decision-flow.md

## Goal

Expose the graph construction pipeline in the web UI and daemon APIs.

## Modules touched

Daemon API, web assets, graph inspection services.

## Inputs

Timeline, extraction runs, session graph, central merge result, validation reports, communities.

## Outputs

Human-readable debug views and JSON payloads.

## Algorithms used

No new algorithms. Uses graph inspection traversals.

## Kuzu writes

None.

## CLI/API surface

Timeline, session graph, merge plan, contested, community, and validation endpoints.

## Unit tests

API payload shape and no stale-daemon behavior.

## Real-data tests

Open web UI against fresh graph and inspect a real session.

## Pass/fail criteria

Every view shows AMO home, graph path, extraction run id, and validation state.

## Must not do

Do not present stale cache as live graph truth.