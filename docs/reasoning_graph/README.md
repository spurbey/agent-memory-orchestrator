# AMO Reasoning Graph Implementation Spec

## Depends on
- ../claude_handbook.md
- ../FINAL_DESIGN_V1.md

## Used by
- implementation/00-implementation-principles.md
- implementation/09-test-and-acceptance-gates.md

## Related docs
- architecture/01-system-purpose.md
- architecture/02-three-level-storage.md
- modules/qwen-contracts.md
- architecture/05-failure-and-safety-model.md

## Purpose

This folder is the canonical implementation specification for AMO Reasoning Graph V1. The older `docs/claude_handbook.md` remains source handover material. This folder normalizes that handover into module contracts, algorithm specifications, graph schema rules, implementation phases, and real-environment validation gates.

AMO exists because Git answers what changed, but not why it changed. The reasoning graph records the decision chain, evidence, code hunks, tests, commits, and version relationships that explain why code took its current form.

## Read Order

1. Start with `architecture/01-system-purpose.md` and `architecture/02-three-level-storage.md`.
2. Read `architecture/05-failure-and-safety-model.md` before any implementation work.
3. Read `modules/qwen-contracts.md` before writing any LLM call.
4. Read `graph_model/node-types.md`, `edge-types.md`, and `status-lifecycle.md` before adding Kuzu writes.
5. Read algorithm docs before coding their modules.
6. Follow the phase docs in `implementation/` in order.
7. Use `examples/` to validate expected graph shape.

## System Shape

The system has three storage levels:

1. Raw session timeline: append-only high-fidelity evidence and transcript events.
2. Session summary graph: cleaned, chunked, extracted, versioned per-session reasoning graph.
3. Central graph: committed, reconciled, append-only graph across sessions.

Hooks only capture. The daemon owns queueing, Qwen, embeddings, Tree-sitter, Kuzu, merge, clustering, and validation. Retrieval ranking is not redesigned in this documentation phase, but graph inspection APIs are specified because they validate the graph and later become retrieval building blocks.

## Documentation Contract

Every document in this folder must include `Depends on`, `Used by`, and `Related docs`. A document without those sections is incomplete.

Every algorithm document must define exact inputs, outputs, thresholds, pseudocode, examples, edge cases, tests, and graph nodes or edges affected.

Every module document must define purpose, inputs, outputs, owned state, public interfaces planned, Kuzu writes, failure modes, and validation checks.