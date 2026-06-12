# Latency, Indexing, And Cache

## Targets

Warm-path targets:

```text
exact file/symbol context: <= 1.5s
edit plan with vector: <= 2s
why/history trace: <= 5s
```

Cold paths may return partial status with follow-up enrichment.

## Required Indexes

- `repo_id + file_path`
- `repo_id + qualified_symbol + symbol_kind`
- `repo_id + active branch/view`
- `commit_sha`
- `version_id`
- relation participant IDs
- relation kind and participant pair
- HarnessCard by session and anchor IDs
- embedding projection ID and document ID

## Early Termination

If exact anchors produce enough high-confidence cards, vector search can be skipped. If exact anchors only cover part of the request, use vector and lexical search for unresolved anchors.

## Cache Policy

Cache derived summaries, anchor resolutions, relation occurrence filters, and card selections by graph version and request hash. Invalidate when the active graph view or projection content hash changes.
