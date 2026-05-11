# Implementation Tracker

Last updated: 2026-05-10  
Baseline design: `docs/FINAL_DESIGN_V1.md`

## Phase Status

- Phase 0: Repo bootstrap - Completed
- Phase 1: Hybrid SQLite memory engine - Implemented as a separate retrieval source
- Phase 2: MCP memory server - Implemented; GraphRAG tools delegate to daemon
- Phase 3: Orchestrator workflow - Pending
- Phase 4: Adapters (Claude/Codex/Omnara) - Implemented
- Phase 5: Kuzu GraphRAG + session work ledger - Implemented foundation plus commit merge engine
- Phase 6: Derived retrieval caches, reclustering, and release hardening - In progress with rebuild/finalize commands
- Phase 7: External connectors - Slack Socket Mode foundation implemented; hosted OAuth/relay not included
- Phase 8: Reasoning Graph V1 detailed docs - In progress; code implementation is blocked until `docs/reasoning_graph/` is reviewed and accepted

## Milestones

## M2.7: Reasoning Graph V1 Documentation System

Goal:

- Normalize `docs/claude_handbook.md` into detailed implementation-ready specs before any further graph rewrite.

Tasks:

- [x] Add `docs/reasoning_graph/` folder structure.
- [x] Add architecture docs with failure/safety model written early.
- [x] Add strict Qwen API contract docs.
- [x] Add graph model docs for nodes, edges, statuses, extraction runs, central versioning, and provenance.
- [x] Add algorithm docs for chunking, semantic drift, Git hunks, Tree-sitter, code nodes, decisions, relationships, entity resolution, dedupe, dependency propagation, and Leiden.
- [x] Add module docs and implementation phase docs with real-data gates.
- [x] Add examples for NDK changes, same-file chunks, reverts, contested decisions, and code-query flow.

Acceptance:

- Code implementation of the full Reasoning Graph V1 rewrite must not start until the docs under `docs/reasoning_graph/` are reviewed and accepted.
- Every reasoning graph doc must include populated `Depends on`, `Used by`, and `Related docs` sections.

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
- [x] Route new GraphRAG MCP calls to daemon by default.
- [x] Return `requires_daemon=true` when daemon is unavailable instead of opening Kuzu directly.

Acceptance:

- Claude and Codex can use the same MCP server for explicit GraphRAG retrieval and legacy memory compatibility.

## M2.5: Kuzu GraphRAG + Session Work Ledger

Goal:

- Pivot primary memory architecture to daemon-owned Kuzu, capture-only hooks, event-triggered Qwen processing, and Git-linked work provenance.

Tasks:

- [x] Add `.amo-spool/` to `.gitignore` and keep generated spool evidence out of the repo.
- [x] Add modular graph subsystems: daemon client, evidence drain, trigger detector, session graph builder, diagnostics, and work ledger.
- [x] Keep hooks capture-only; no Qwen, retrieval, or graph writes inside hook path.
- [x] Add daemon graph endpoints for drain, graph status, session context, raw evidence search, work trace, and debug checks.
- [x] Add durable evidence cursors and idempotent evidence drain.
- [x] Add trigger detection for write/edit, test after write, git operations, explicit finalize prompts, and stop-with-pending-write.
- [x] Add bounded evidence-window cleaning before Qwen graph extraction.
- [x] Add Qwen-backed GraphDelta extractor with deterministic fallback for tests/offline failure.
- [x] Add latest per-session `ContextSnapshot` built only after trigger windows.
- [x] Add provenance trace nodes/edges: `RawEvidenceRef -> CleanedEvidenceWindow -> GraphDelta -> WorkChange/Decision/File/TestRun`.
- [x] Add answer-quality gates and noisy draft quarantine for early graph drain pollution.
- [x] Add deterministic graph consolidation edges for duplicate/refine/supersede/contradict candidates plus topic clusters.
- [x] Add rebuildable lexical GraphRAG retrieval cache foundation for answer-grade graph nodes.
- [x] Add local Git commit metadata, changed files, diff stats, and patch-id support.
- [x] Add daemon-owned `CommitMergeEngine` for dry-run/apply session finalization into central committed nodes.
- [x] Add hybrid deterministic/Qwen merge classification with low-confidence review candidates.
- [x] Add non-destructive version edges for `COMMITTED_AS`, `DUPLICATE_OF`, `REFINES`, `SUPERSEDES`, `CONTRADICTS`, and `MODIFIES`.
- [x] Keep raw/support nodes as provenance and exclude them from answer-grade promotion.
- [x] Add `graph-finalize-session` CLI/API for manual repair and commit-bounded promotion.
- [x] Add `graph-rebuild-central` CLI/API for dry-run and backup/replay/swap rebuilds from raw evidence.
- [x] Add session cockpit and dependency-free 3D central graph view for graph inspection.
- [x] Add CLI debug commands for hooks, drain, Qwen, graph, and retrieval.
- [x] Add unit tests for trigger detection, drain idempotency, session context build, daemon-required MCP behavior, and Git work ledger.

Acceptance:

- Read-only prompts remain raw evidence only.
- Write/test/git/finalize triggers create session graph nodes and a clean current context snapshot.
- Each extracted knowledge node is traceable back to raw evidence refs, a cleaned evidence window, and the GraphDelta that created it.
- MCP GraphRAG tools use daemon by default and fail clearly if daemon is down.
- Git commit traces are available for linking work changes to code history.
- Commit/finalize boundaries can promote draft answer-grade work into central committed graph memory with dry-run review.
- Fresh central graph rebuilds can be planned and applied from raw evidence without using durable normal-drain cursors.

## M2.6: External Connector Foundation

Goal:

- Let local AMO capture external collaboration context without exposing local storage or local LLMs.

Tasks:

- [x] Add modular connector package layout under `src/agent_memory_orchestrator/connectors`.
- [x] Add Slack app manifest generation for local Socket Mode.
- [x] Add one-click Slack setup URL generation with manifest JSON prefilled.
- [x] Add Slack App Manifest API bootstrap using a temporary app configuration token.
- [x] Add interactive Slack setup wizard for token paste, validation, ID derivation, and local token save.
- [x] Add local Slack config and optional token storage under `AMO_HOME/.secrets`.
- [x] Add Slack message normalization and relevance gates.
- [x] Capture Slack messages into append-only raw evidence with `source_app=slack`.
- [x] Enforce reply-only-when-mentioned behavior.
- [x] Add tagged Slack answer mode that queries local GraphRAG and posts compact node/evidence/commit refs.
- [x] Add connector finalize event so `graph-drain` can create cleaned windows and GraphDelta nodes.
- [x] Add optional WebSocket runner behind the `slack` extra.

Acceptance:

- A user can bring Slack `xapp`/`xoxb` tokens, run AMO locally in Socket Mode, capture relevant sent Slack messages, answer tagged channel mentions through GraphRAG, finalize a Slack session, and drain it into the Kuzu graph without exposing localhost.

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

- Kuzu GraphRAG retrieval currently has deterministic graph ranking, Qwen planning/compression, commit merge promotion, and a rebuildable lexical cache; FAISS/BGE cross-encoder graph-derived caches are still pending.
- Qwen extraction falls back deterministically when Ollama is unavailable; production installs should treat Qwen availability as required and monitor debug latency.
- Central graph reclustering/consolidation has a deterministic foundation and now runs after finalize/rebuild; deeper graph-derived vector cache evaluation is still pending.
- Qwen merge classification is wired for ambiguous relations; broader eval fixtures and model-latency budgets still need expansion.
- Hybrid SQLite tools remain present and can confuse operators if docs/CLI labels imply they are the GraphRAG path.

## Change Control

Rules:

1. Any architecture change must update `docs/FINAL_DESIGN_V1.md`.
2. Any scope change must update this tracker with milestone impact.
3. Keep one source of truth for state-machine rules in `FINAL_DESIGN_V1.md`.
