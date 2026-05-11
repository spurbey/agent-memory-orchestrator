# Real Data Selection For Implementation Gates

## Depends on
- 09-test-and-acceptance-gates.md
- 01-phase-raw-timeline.md
- 03-phase-code-analysis.md

## Used by
- 02-phase-session-graph.md
- 03-phase-code-analysis.md
- 05-phase-central-merge.md

## Related docs
- ../modules/raw-evidence-ledger.md
- ../modules/codex-transcript-importer.md
- ../algorithms/same-file-resolution.md
- ../algorithms/code-node-versioning.md

## Purpose

Pin the first real AMO/Codex data set for Reasoning Graph V1 implementation gates. This document prevents implementation from quietly falling back to synthetic-only fixtures.

No graph data is cleared, rebuilt, or mutated by this selection. The selected paths are inputs for later test commands.

## Repository State At Selection

Repository:

```text
C:\Users\sumit\Downloads\Dora\agent-memory-orchestrator
```

Known unrelated dirty files that must stay isolated from this implementation gate:

```text
src/agent_memory_orchestrator/graph/cache.py
src/agent_memory_orchestrator/graph/service.py
src/agent_memory_orchestrator/graph/store.py
tests/test_graph_rag.py
```

These files existed as dirty changes before this data selection. Do not mix them into reasoning-graph implementation commits unless intentionally reviewed.

## Primary Real Session

Use this session for the first raw timeline, session graph, same-file resolution, and code-node versioning gates:

```text
session_id: 019e08eb-8f1f-7381-8f25-59344c4ac8a9
transcript_path: C:\Users\sumit\.codex\sessions\2026\05\09\rollout-2026-05-09T00-33-35-019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl
evidence_file: C:\Users\sumit\.agent-memory-orchestrator\.evidence\2026-05-08.jsonl
```

Why this session:

- it has real AMO hook evidence
- it has a real Codex transcript path that exists locally
- it has user prompts, tool events, stop events, and apply-patch writes
- it repeatedly edits the same file through real Codex session evidence
- it maps to a real AMO commit touching the same implementation area

## Repeated Same-File Evidence

Repeated file:

```text
src/agent_memory_orchestrator/install_service.py
```

Evidence ids from the selected session:

```text
raw_639e2963e72e4e3bb063042eeb221afd
raw_3ce293ed37ce4d7ebabae7c1116bdd69
raw_850a43197504432bafb15e01f384af28
raw_2c4610ee5d594dafbd13bde52b9f6a42
```

These are real `apply_patch` events from AMO hook evidence. They are valid for the same-file resolution gate because they come from the captured Codex session timeline rather than a synthetic commit sequence.

Expected implementation use:

- raw timeline importer reads these evidence refs
- transcript importer adds assistant/user context from `transcript_path`
- chunker separates or rejoins topic segments
- same-file resolver decides continuation, refinement, supersession, or unrelated same-file edits
- code-node versioning preserves older nodes and links newer nodes with version edges only when the algorithm justifies it

## Code Hunk Commit

Use this real AMO commit for Git hunk and code-node validation:

```text
commit: c5326f8
subject: fix(codex-hooks): harden capture hook execution
```

Changed files:

```text
README.md
src/agent_memory_orchestrator/cli.py
src/agent_memory_orchestrator/config.py
src/agent_memory_orchestrator/hook.py
src/agent_memory_orchestrator/install_service.py
src/agent_memory_orchestrator/model_manager.py
tests/test_hook_cli.py
tests/test_install_service.py
tests/test_model_manager.py
```

Why this commit:

- it is a real AMO code commit
- it includes `install_service.py`, matching the repeated same-file evidence
- it includes implementation and test files
- it is suitable for `git show --unified=0 c5326f8 -- <file>` hunk parsing

## Required Gate Commands Later

These are planned commands for later implementation phases. They are listed here only to define expected inputs:

```powershell
amo-cli graph-import-session --session-id 019e08eb-8f1f-7381-8f25-59344c4ac8a9
amo-cli graph-build-session --session-id 019e08eb-8f1f-7381-8f25-59344c4ac8a9 --extraction-run new --apply
amo-cli graph-validate-session --session-id 019e08eb-8f1f-7381-8f25-59344c4ac8a9
amo-cli graph-finalize-session --session-id 019e08eb-8f1f-7381-8f25-59344c4ac8a9 --commit c5326f8 --extraction-run <id> --dry-run
```

## Pass Conditions For This Data Set

The implementation gate passes only if:

- raw timeline contains imported assistant messages from the transcript, not only hook events
- apply-patch events become `ToolUse` and `ToolResult` timeline nodes
- repeated `install_service.py` writes are detected from real evidence ids
- commit `c5326f8` produces parsed `CodeHunk` objects from real Git diff output
- code nodes are AST-bounded where grammar exists or explicitly marked `ast_status=unparsed`
- same-file resolution does not link unrelated topics just because the file path matches
- answer-grade nodes carry evidence refs and extraction run provenance

## Known Coverage Gap

No natural contested-decision case is selected here. If no natural contested case exists when dependency propagation is implemented, use a controlled test graph path seeded from real extracted nodes and record that as the known V1 contested natural-data gap described in `09-test-and-acceptance-gates.md`.

That fallback validates traversal mechanics only. It does not prove natural contested-case coverage.

## Stop Rules

- Do not use this document as permission to mutate Kuzu.
- Do not clear or rebuild graph data during Work 1.
- Do not satisfy same-file resolution with synthetic fixtures.
- Do not include the unrelated dirty graph/retrieval files in docs-only commits.
