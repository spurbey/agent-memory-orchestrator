# System Purpose

## Depends on
- ../README.md
- ../../claude_handbook.md

## Used by
- 02-three-level-storage.md
- 04-data-flow-end-to-end.md
- ../implementation/00-implementation-principles.md

## Related docs
- ../graph_model/central-versioning-rules.md
- ../examples/code-query-flow.md

## Purpose

AMO is a reasoning layer above Git. Git stores file snapshots, branches, commits, and diffs. AMO stores why an agent or user decided to change code, which evidence supported that decision, what code region changed, what tests validated it, and how that reasoning evolved across sessions.

The product goal is not chat memory. The product goal is durable code-work reasoning. A future agent should be able to ask: `why does this block exist?`, `what decision superseded the old implementation?`, `which test validated this fix?`, or `which downstream decisions became contested after this dependency changed?`

## Git Versus AMO

Git is authoritative for code contents. AMO must never replace Git as source of truth for files. AMO stores pointers to Git commits, patch ids, file paths, and code snippets selected from hunks and AST nodes.

Git answers:

```text
What changed in this commit?
What does this file look like at commit X?
Who changed this line?
```

AMO answers:

```text
Why was this code changed?
Which decision produced this hunk?
Which earlier decision did this supersede?
Which tests validated the change?
What unresolved conflict affects this file?
```

## Required Guarantees

AMO must preserve history. No answer-grade graph node is deleted when a new decision arrives. Old knowledge is refined, superseded, contradicted, abandoned, or contested through statuses and edges.

AMO must be evidence-backed. Every answer-grade node must trace back to raw evidence, cleaned evidence, extraction run, session id, and commit id when code was changed.

AMO must be local-first. Hooks, Kuzu, Qwen, embeddings, graph rebuilds, and debug UI run locally unless a later explicit hosted connector changes that design.

## Non-Goals In This Spec

This spec does not define final retrieval ranking UX. It defines graph construction, versioning, and graph inspection APIs. Retrieval can later use the same nodes, edges, communities, and inspection paths.