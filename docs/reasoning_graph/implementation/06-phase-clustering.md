# Phase 6: Clustering

## Depends on
- 05-phase-central-merge.md
- ../algorithms/leiden-community-detection.md

## Used by
- 08-phase-web-debugging.md
- future retrieval work

## Related docs
- ../graph_model/node-types.md
- ../modules/graph-validation.md

## Goal

Run Leiden community detection over central graph for navigation and future retrieval scoping.

## Modules touched

Community module, Kuzu store, graph validation.

## Inputs

Central graph nodes and weighted edges.

## Outputs

Community ids, labels, membership edges, diagnostics.

## Algorithms used

Kuzu to NetworkX to igraph to Leiden modularity partition, then deterministic top-term community labeling from member labels, summaries, file paths, entity fields, node-kind weights, and internal degree weights. Qwen label generation is optional and can only improve the display label under the strict contract.

## Kuzu writes

`Community`, `MEMBER_OF`, community metadata on nodes.

## CLI/API surface

`graph-community-run --apply`.

## Unit tests

Weighted fixture graph creates expected communities. Deterministic labels are stable across repeated runs and exclude raw ids, hashes, and generic graph terms.

## Real-data tests

Run on fresh central graph and inspect clusters.

## Pass/fail criteria

Community metadata exists and does not change decision status. Community labels are deterministic without Qwen and remain valid when Qwen is unavailable.

## Must not do

Do not use community membership as proof of truth.
