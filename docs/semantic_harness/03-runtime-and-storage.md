# Runtime And Storage

## Runtime Model

Semantic Harness runs as a local daemon first. It must be usable without hosted state ownership. Hosted models can be providers, but local state remains authoritative.

## Store Responsibilities

Use separate stores when it improves reliability, but keep one logical graph identity.

- Raw evidence ledger: append-only source records and AMO imports.
- Graph store: files, symbols, code regions, versions, relation occurrences, cards, lineage, provenance.
- SQLite ledgers: jobs, projections, evals, idempotency, import mapping, feedback status.
- Vector cache: rebuildable embeddings and FAISS indexes.
- Stage artifacts: reproducible snapshots for bootstrap, update, eval, and migration.

## Deterministic IDs

Harness IDs are content-derived where possible:

```text
repo_id = stable repo identity from configured UUID, normalized remote, or Git root fallback
file_id = file:<repo_id>:<normalized_file_path>
symbol_id = symbol:<repo_id>:<normalized_file_path>:<qualified_name>:<symbol_kind>
code_region_id = region:<repo_id>:<normalized_file_path>:<region_kind>:<stable_span_or_content_hash>
commit_id = commit:<repo_id>:<full_sha>
work_window_id = work:<repo_id>:<session_id>:<commit_or_window_hash>
relation_occurrence_id = relocc:<repo_id>:<commit_sha>:<relation_kind>:<participant_hash>
harness_card_id = hcard:<repo_id>:<session_id>:<intent>:<card_hash>
```

AMO IDs are stored in `ExternalAmoRef` nodes or `IMPORTED_FROM_AMO` edge metadata.

## Idempotency

Every bootstrap and update stage must be replayable. Re-running the same input should produce the same primary IDs and either reuse or supersede projections deterministically.

## Qwen Degraded Mode

If Qwen is unavailable, the daemon still updates deterministic structural graph state and marks semantic enrichment as missing or pending. Structural harness queries remain available.
