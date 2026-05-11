# Raw Evidence Ledger

## Depends on
- ../architecture/02-three-level-storage.md
- ../graph_model/provenance-and-evidence.md

## Used by
- codex-transcript-importer.md
- session-timeline-builder.md
- extraction-run-manager.md

## Related docs
- ../architecture/05-failure-and-safety-model.md
- ../implementation/01-phase-raw-timeline.md

## Purpose

Store append-only raw hook and connector evidence with stable ids, hashes, paths, offsets, source app, event name, and timestamps.

## Inputs

Hook payloads, connector payloads, and imported transcript references.

## Outputs

`RawEvidenceRef` records and Kuzu provenance nodes.

## Owned state

`AMO_HOME/.evidence/*.jsonl`, fallback spool evidence, and evidence cursor metadata.

## Public interfaces planned

- `append(payload) -> RawEvidenceRef`
- `iter(session_id, range) -> records`
- `find(evidence_id) -> record`

## Kuzu writes

Creates `RawEvidenceRef` nodes and `EVIDENCED_BY` edges when drained.

## Failure modes

AMO home blocked falls back to spool. Hash collision fails closed. Malformed JSON is quarantined with diagnostics.

## Validation checks

Evidence id resolves to file path and offset. Hash matches stored payload. Raw evidence is never promoted as answer-grade memory.