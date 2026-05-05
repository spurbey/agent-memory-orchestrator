# Implementation Tracker

Last updated: 2026-05-06  
Baseline design: `docs/FINAL_DESIGN_V1.md`

## Phase Status

- Phase 0: Repo bootstrap - Completed
- Phase 1: Personal local memory engine - Implemented vertical slice
- Phase 2: MCP memory server - Pending
- Phase 3: Orchestrator workflow - Pending
- Phase 4: Adapters (Claude/Codex) - Pending
- Phase 5: Hardening and release prep - Pending

## Milestones

## M0: Bootstrap and Contracts

Goal:

- Establish project skeleton and canonical interfaces.

Tasks:

- [x] Create package scaffold and base docs.
- [x] Define initial SQLite schema.
- [x] Add typed domain models (`Session`, `Event`, `Memory`, `Round`, `Decision`).
- [x] Add config validation and defaults for local-only mode.
- [x] Add CLI entry points (`init-db`, `ingest`, `search`, `export`, `orchestrate`).

Acceptance:

- CLI starts and DB initializes cleanly on a fresh machine.

## M1: Personal Local Memory Pipeline

Goal:

- Persist events and produce typed, searchable, observable memory artifacts.

Tasks:

- [x] Implement ingestion normalization for Claude/Codex JSONL (basic v0).
- [x] Add hook payload ingestion entrypoint.
- [x] Add Codex rollout JSONL import for `~/.codex/sessions`.
- [x] Add Codex-compatible hook responses with `additionalContext` on `UserPromptSubmit`/`SessionStart`.
- [x] Add redaction before persistence.
- [x] Add typed chunking for prose/code/diff/stacktrace/test/tool/json content.
- [x] Add canonical `memory_units`, `chunks`, KG, summaries, and observability tables.
- [x] Add rule extraction with fixed confidence table.
- [x] Add local embedding interface targeting BGE-M3 with deterministic fallback.
- [x] Add FTS5/BM25, vector, KG retrieval with RRF and rerank fallback.
- [x] Add memory-level consolidation/versioning baseline.
- [x] Add metrics, replayable retrieval candidates, and index rebuild command.
- [x] Add local daemon Web UI for sessions, events, memories, retrieval runs, and returned candidates.
- [x] Add clean DB rebuild workflow for replaying Codex sessions into a disposable canonical store.
- [x] Add agent-ready context-pack builder with budget control, provenance, exclusions, and durable-memory preference.
- [x] Add optional local cross-encoder reranker interface with lexical fallback and retrieval trace metadata.
- [x] Add optional FAISS vector cache build/search with SQLite vector scan fallback.
- [x] Add unit/integration tests for Phase 1 pipeline and algorithms.

Acceptance:

- Given input transcripts/hooks, memory search returns relevant hits with provenance and trace rows.
- Superseded memories are deprioritized unless historical retrieval is requested.
- Pipeline/retrieval/consolidation observability rows are inspectable.

## M2: MCP Memory Tools

Goal:

- Expose shared memory operations through local MCP.

Tasks:

- [ ] Implement `memory_write`.
- [ ] Implement `memory_search`.
- [ ] Implement `memory_timeline`.
- [ ] Implement `memory_export` and `memory_import`.
- [ ] Add MCP contract tests.

Acceptance:

- Claude and Codex can use the same MCP server to read/write memory.

## M3: Orchestrator Core

Goal:

- Enforce deterministic two-agent consensus before user decision.

Tasks:

- [ ] Implement orchestration state machine.
- [ ] Implement round submission validation.
- [ ] Implement blocker/confidence gate logic.
- [ ] Implement `orchestrator_*` MCP tools.
- [ ] Add tests for all valid/invalid transitions.

Acceptance:

- A plan cannot reach `approved` without explicit user decision.

## M4: Adapter Integrations

Goal:

- Smooth ingestion from real agent sessions and optional visibility integrations.

Tasks:

- [ ] Add Claude adapter (session/hook artifact normalization).
- [ ] Add Codex adapter (session artifact normalization).
- [ ] Add optional Omnara adapter (non-authoritative).
- [ ] Add redaction hooks before persistence.

Acceptance:

- Live sessions from both agents generate usable shared memory.

## M5: Release Readiness

Goal:

- Public-safe, reproducible, and documented v1 release.

Tasks:

- [ ] Add end-to-end demo script.
- [ ] Add reproducible local run instructions.
- [ ] Add CI (lint + tests).
- [ ] Threat model notes and privacy defaults.
- [ ] Changelog and release tag.

Acceptance:

- Public repo can be cloned and run locally with one setup guide.

## Current Risks

- Optional BGE-M3/FAISS/cross-encoder dependencies are not mandatory; fallback behavior is deterministic but less semantically accurate.
- Optional model loaders are local-only. Models must already be available locally; AMO should not download model artifacts during memory operations.
- Phase 2 Work Ledger is architecturally reserved but not implemented.

## Change Control

Rules:

1. Any architecture change must update `docs/FINAL_DESIGN_V1.md`.
2. Any scope change must update this tracker with milestone impact.
3. Keep one source of truth for state-machine rules in `FINAL_DESIGN_V1.md`.
