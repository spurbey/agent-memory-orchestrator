# Implementation Tracker

Last updated: 2026-04-30  
Baseline design: `docs/FINAL_DESIGN_V1.md`

## Phase Status

- Phase 0: Repo bootstrap - Completed
- Phase 1: Core data and memory pipeline - In progress
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

## M1: Memory Pipeline

Goal:

- Persist events and produce searchable memory artifacts.

Tasks:

- [x] Implement ingestion normalization for Claude/Codex JSONL (basic v0).
- [x] Implement extraction pipeline (summary, tags, importance) (basic v0).
- [x] Implement vector generation interface with local baseline embedder.
- [x] Implement retrieval (`lexical + vector + rerank`) (basic v0).
- [x] Add unit tests for extraction/retrieval and orchestration transitions.

Acceptance:

- Given input transcripts, memory search returns cross-session relevant hits.

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

- Test execution can fail under restricted temp directory permissions in some sandboxed environments.
- Embedding quality may be low until a stronger local/embed provider is added.

## Change Control

Rules:

1. Any architecture change must update `docs/FINAL_DESIGN_V1.md`.
2. Any scope change must update this tracker with milestone impact.
3. Keep one source of truth for state-machine rules in `FINAL_DESIGN_V1.md`.
