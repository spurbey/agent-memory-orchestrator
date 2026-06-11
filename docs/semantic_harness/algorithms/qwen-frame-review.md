# Qwen Frame Review

## Purpose

Gate Qwen semantic proposals before graph mutation.

## Inputs

- Qwen output
- packet evidence
- commit facts
- hunk mappings
- validation
- active graph context

## Outputs

`accepted`, `review_only`, `rejected`, `quarantined`, or `pending_enrichment` frames.

## Algorithm

```text
1. Validate JSON and schema.
2. Verify cited evidence exists.
3. Verify cited code entities exist or are review-safe mappings.
4. Compare statement with commit/hunk facts.
5. Check confidence threshold by frame kind.
6. Accept, demote, reject, quarantine, or mark pending.
```

## Confidence Scoring

Use Qwen confidence only as one signal. Deterministic evidence alignment and code mapping dominate final acceptance.

## Failure Modes

Invalid JSON is quarantined. Unsupported statement is rejected. Weak but plausible statement becomes `review_only`. Model unavailable becomes `pending_enrichment`.

## Product Usage

Prevents hallucinated reasons from becoming cards or graph truth.

## Real-Session Eval

Use an AMO packet with known bad reasoning and verify it stays `review_only` or rejected.

## Worked Example

Input: Qwen says `fallback retrieval was changed`, but commit touches UI layout only.

Intermediate: evidence exists, code mapping mismatch, commit alignment low.

Output: rejected with reason `commit_mismatch`.
