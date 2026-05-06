# Implementation Tracker

Last updated: 2026-05-07  
Baseline design: `docs/FINAL_DESIGN_V1.md`

## Phase Status

- Phase 0: Repo bootstrap - Completed
- Phase 1: Personal local memory engine - Implemented vertical slice
- Phase 2: MCP memory server - Implemented
- Phase 3: Orchestrator workflow - Pending
- Phase 4: Adapters (Claude/Codex/Omnara) - Implemented
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
- [x] Add Phase 1.3 retrieval-quality hardening for context-pack ordering, IDE noise, raw tool JSON, and user-question suppression.
- [x] Add retrieval eval fixture coverage for Codex hook memory recall and forbidden packed-context terms.
- [x] Add explicit local model management commands for preset selection, status, download, and preflight.
- [x] Add local SQLite knowledge graph API and interactive 3D `/graph` visualization page.
- [x] Add Phase 1 hardening for low-score context cutoffs, duplicate memory suppression, KG versioning edges, graph filters, and expanded retrieval eval fixtures.
- [x] Add unit/integration tests for Phase 1 pipeline and algorithms.

Acceptance:

- Given input transcripts/hooks, memory search returns relevant hits with provenance and trace rows.
- Superseded memories are deprioritized unless historical retrieval is requested.
- Pipeline/retrieval/consolidation observability rows are inspectable.

## M2: MCP Memory Tools

Goal:

- Expose shared memory operations through local MCP.

Tasks:

- [x] Implement modular `MemoryMcpToolService` behind thin FastMCP registration.
- [x] Implement `memory_write`.
- [x] Implement `memory_search`.
- [x] Implement `memory_context_pack`.
- [x] Implement `memory_timeline`.
- [x] Implement `memory_export` and `memory_import`.
- [x] Add explicit `tool_contracts`.
- [x] Add MCP contract tests.

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

- [x] Add adapter package with shared normalized event contract.
- [x] Add Claude adapter (session/hook artifact normalization).
- [x] Add Codex adapter (session artifact normalization).
- [x] Add optional Omnara adapter (non-authoritative).
- [x] Route `MemoryService.normalize_event_payload` through adapter router.
- [x] Keep redaction in `MemoryService.add_event` before persistence.
- [x] Add adapter contract tests.

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
- Retrieval eval coverage is still small; expand with real user queries before treating auto-injection as production-grade.
- Duplicate/supersession rules are deterministic heuristics; keep reviewing `consolidation_decisions` before trusting automatic replacement at large scale.
- Phase 2 Work Ledger is architecturally reserved but not implemented.

## Change Control

Rules:

1. Any architecture change must update `docs/FINAL_DESIGN_V1.md`.
2. Any scope change must update this tracker with milestone impact.
3. Keep one source of truth for state-machine rules in `FINAL_DESIGN_V1.md`.
