# Semantic Review

## Purpose

Semantic review converts Qwen proposals into accepted, review-only, rejected, or pending enrichment states.

## Inputs

- Qwen proposal JSON
- commit and hunk facts
- hunk-to-symbol mappings
- work-window evidence
- validation output
- current graph context

## Review Outcomes

- `accepted`: graph-safe semantic frame.
- `review_only`: useful but not answer-grade.
- `rejected`: unsupported or contradicted.
- `quarantined`: malformed, private, unsafe, or hallucinated.
- `pending_enrichment`: Qwen unavailable or timed out.

## Acceptance Rules

A frame can be accepted only when:

- cited evidence exists in the packet
- cited code entities exist or map through review-safe provenance
- statement aligns with commit/hunk facts
- confidence meets the threshold for that frame kind
- no private path or raw transcript ID leaks into product memory

## Quarantine Feedback

Quarantined frames are retained as diagnostics. They support prompt/schema improvement and false-positive evals. They must not appear in default harness cards.
