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
7. Create initial FileVersion, SymbolVersion, and CodeRegionVersion snapshots.
8. Build summaries for files, symbols, regions, and docs.
9. Build lexical and vector projections over summaries.
10. Run bootstrap evals.
```

## Outputs

- Repo/File/Symbol/CodeRegion graph
- structural edges
- initial versions
- retrieval projection
- bootstrap eval report

## Zero-History Behavior

Without AMO history or prior work windows, the harness can still answer `edit_plan` and `file_context` from structural context.

Status should be `partial_structural` when useful structure exists but historical work reasoning is absent.

## Failure Modes

- Parser missing: use file-level and lexical fallback.
- Unsupported language: create File and limited CodeRegion anchors.
- Huge generated file: exclude or summary-only based on policy.
- Ambiguous symbol: return partial coverage and request a stronger anchor.
