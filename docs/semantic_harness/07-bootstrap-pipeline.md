# Bootstrap Pipeline

## Purpose

Bootstrap builds useful structural context before any work history exists.

## Inputs

- repo root
- file tree
- language/parser availability
- docs, README, docstrings, config files
- optional LSP/static analysis adapters

## Stages

```text
1. Resolve repo_id.
2. Walk source, docs, config, and tests.
3. Normalize file paths.
4. Parse symbols where supported.
5. Create lazy CodeRegions only for docs/config regions that need semantic anchors.
6. Extract imports, definitions, calls, and containment.
7. Extract deterministic DocSection and DocString nodes.
8. Attach exact doc mentions to file/symbol anchors.
9. Create initial FileVersion, SymbolVersion, and CodeRegionVersion snapshots.
10. Build summaries for files, symbols, regions, and docs.
11. Build lexical and vector projections over summaries.
12. Run bootstrap evals.
```

## Outputs

- Repo/File/Symbol/CodeRegion graph
- structural edges
- doc/docstring support edges
- initial versions
- retrieval projection
- bootstrap eval report

## Deterministic Doc Semantics

Bootstrap may create:

```text
DocSection -> MENTIONS_FILE -> File
DocSection -> MENTIONS_SYMBOL -> Symbol
DocString -> DOCUMENTS_FILE -> File
DocString -> DOCUMENTS_SYMBOL -> Symbol
```

These links require exact repo-relative paths, parser-backed docstring ownership, or exact symbol labels. Embeddings and Qwen may later propose fuzzy doc links, but those proposals are not graph truth until deterministic review accepts them.

## Zero-History Behavior

Without AMO history or prior work windows, the harness can still answer `edit_plan` and `file_context` from structural context.

Status should be `partial_structural` when useful structure exists but historical work reasoning is absent.

## Failure Modes

- Parser missing: use file-level and lexical fallback.
- Unsupported language: create File and limited CodeRegion anchors.
- Huge generated file: exclude or summary-only based on policy.
- Ambiguous symbol: return partial coverage and request a stronger anchor.
