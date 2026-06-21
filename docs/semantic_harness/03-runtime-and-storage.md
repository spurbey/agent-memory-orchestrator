# Runtime And Storage

## Runtime Model

Semantic Harness runs as a local daemon first. It must be usable without hosted state ownership. Hosted models can be providers, but local state remains authoritative.

## Store Responsibilities

Use separate stores when it improves reliability, but keep one logical graph identity.

- Raw evidence ledger: append-only source records and AMO imports.
- HelixDB graph store: files, symbols, code regions, versions, relation occurrences, cards, lineage, provenance.
- SQLite ledgers outside the harness graph: jobs, evals, idempotency, import mapping, and feedback status.
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

## Graph And Projection Identity

The structural graph snapshot ID tracks structural identity, not operational state:

```text
graph_snapshot_id =
  hash(
    graph_schema_version
    repo_id
    sorted node IDs
    sorted edge keys: source_id + kind + target_id
  )
```

Do not include mutable metadata, summaries, relation weights, counters, or feedback status in `graph_snapshot_id`. Those fields can change during enrichment or eval feedback without changing the structural graph shape.

Projection identity is derived from graph identity plus projection policy:

```text
projection_id =
  hash(
    projection_version
    graph_snapshot_id
  )
```

This gives later Helix-native or external vector caches a stable invalidation boundary:

```text
same graph_snapshot_id + same projection_version -> reusable projection
same graph_snapshot_id + new projection_version -> rebuild projection
new graph_snapshot_id -> rebuild projection and derived vector cache
```

## Qwen Degraded Mode

If Qwen is unavailable, the daemon still updates deterministic structural graph state and marks semantic enrichment as missing or pending. Structural harness queries remain available.
