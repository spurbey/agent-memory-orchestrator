# V2 Production Stage Plan

This document maps the reset artifacts under `.tmp/reasoning-graph-v2-reset-2026-05-14` to production code, validation gates, and commit boundaries.

## Intent

The graph is for answering user queries about why and how code changed.

The graph truth model is:

```text
whole session evidence
-> commit-backed work packets
-> LLM-extracted reasoning nodes
-> Git hunk and AST code truth
-> isolated Kuzu session graph
-> SQLite/FTS/vector retrieval docs
-> graph-expanded answer
```

Raw Codex/tool-call events are support evidence only. They must not become answer-grade graph nodes unless a later stage deliberately converts them into a validated reasoning, commit, code, symbol, or evidence-ref node.

## Stage Map

| Stage | Purpose | Production Owner | Current Status | Acceptance Gate |
| --- | --- | --- | --- | --- |
| 01 raw JSONL | Preserve the whole evidence file and identify sessions/transcripts | raw evidence ledger and transcript import modules | artifact-proven only | raw record counts and selected transcript paths match source files |
| 02 reasoning evidence view | Convert noisy raw events into concise user/agent/read/write/validation evidence refs | new production packet-prep module needed | artifact-proven only | no raw tool-call payloads in LLM-facing text, support refs remain traceable |
| 03 work packets | Build commit-backed work packets from the whole session | new production packet builder needed, using Git truth and Stage 02 evidence refs | artifact-proven with strict Stage 3B output | every packet resolves to a real commit, fake commits quarantined |
| 04 reasoning extraction | Extract `Problem`, `Cause`, `Decision`, `Fix`, `Constraint`, `OpenQuestion` packet-wise | new production LLM runner and validator needed | Colab runner plus merged accepted output | output split into accepted/needs_review/rejected, refs are packet-local evidence refs only |
| 05 code graph | Attach deterministic code/version truth | `reasoning_graph.code_analysis`, `work_changes`, `relationships`, `validation`, `session_query`, `session_runtime` | production modules committed | Git hunks map to CodeNodes, symbol versions are valid, Fix nodes link to code |
| 06 isolated Kuzu | Write validated session graph without mutating central graph | Kuzu graph store plus session graph writer | artifact-proven, compact Kuzu reads supported | node/edge manifests match Kuzu readback and all edges resolve |
| 07 retrieval | Build answer retrieval over graph docs | `reasoning_graph.retrieval`, graph service, CLI, daemon | production modules committed | exact/BM25/vector fusion retrieves expected packets/commits/symbols |
| 08 central merge | Promote an accepted session graph into central memory | central merge/versioning engine | not started for V2 reset | isolated graph passes retrieval eval before merge |

## Production Rules

- Stage 01 must ingest the whole raw JSONL file, not a focused window.
- Stage 02 may compress evidence but must keep provenance refs.
- Stage 03 must use Git commits as the work-packet spine.
- Stage 04 is the only required LLM stage in this flow.
- Stage 05 and Stage 06 are deterministic and should not call an LLM.
- Stage 07 can use embeddings and ranking, but graph expansion must keep citations to packet, commit, reasoning, code, and symbol nodes.
- Central graph writes must wait until isolated graph validation and retrieval evaluation pass.

## Commit Boundaries

Use separate commits for:

1. Packet/evidence productionization for Stages 01-03.
2. LLM extraction runner and validator for Stage 04.
3. Deterministic code graph spine for Stage 05.
4. Isolated Kuzu graph writer for Stage 06.
5. Retrieval index and query runtime for Stage 07.
6. Central merge/versioning for Stage 08.

Each commit must include:

- production code under `src/`
- focused unit tests under `tests/`
- one real-artifact smoke or review note in `.tmp/` for local debugging, not committed unless intentionally documented
- command output summarized in the commit handoff or review note

## Current Committed Baseline

- `40c27c7` wires indexed graph retrieval.
- `cb949f2` adds deterministic session graph spine.
- `4013a49` filters generic central graph-search terms.

The remaining production gaps are Stages 01-04 and the Stage 06 compact graph writer/coordinator. The reset artifacts already prove the intended output shape; the next work is to turn those artifact scripts into importable modules and CLI/daemon commands.

## Next Production Step

Promote Stages 01-03 into one packet-prep module:

```text
raw JSONL + transcript paths
-> evidence refs
-> commit-backed work packets
-> packet validation report
```

The first production command should produce the same kind of output as:

```text
.tmp/reasoning-graph-v2-reset-2026-05-14/03b_reasoning_work_packets_strict_validation/reasoning_work_packets.json
```

but from reusable code under `src/agent_memory_orchestrator/reasoning_graph/`.
