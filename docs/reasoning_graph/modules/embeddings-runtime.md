# Embeddings Runtime

## Depends on
- ../architecture/05-failure-and-safety-model.md

## Used by
- ../algorithms/semantic-drift-detection.md
- ../algorithms/entity-resolution.md
- ../algorithms/decision-deduplication.md
- ../algorithms/code-node-versioning.md

## Related docs
- qwen-contracts.md
- graph-validation.md

## Purpose

Provide deterministic access to text and code embeddings used by chunking, entity resolution, dedupe, and code queries.

## Inputs

Text snippets, decision summaries, topic windows, code snippets.

## Outputs

Embedding vectors and embedding metadata.

## Owned state

Embedding cache keyed by content hash, model name, model version, and vector dimension.

## Public interfaces planned

- `embed_text(text, purpose) -> vector`
- `embed_code(code, language) -> vector`
- `cosine(a, b) -> float`

## Kuzu writes

May store embedding ids or vector metadata on graph nodes. Large vectors can live in derived cache if Kuzu storage is not appropriate.

## Failure modes

Missing model records `embedding_status=missing`. Algorithms requiring embeddings downgrade confidence or create review candidates.

## Validation checks

Same input produces stable cache hit. Missing embeddings never produce high-confidence semantic decisions.