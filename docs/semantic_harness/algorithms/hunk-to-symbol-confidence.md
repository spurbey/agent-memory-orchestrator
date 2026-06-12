# Hunk To Symbol Confidence

## Purpose

Map Git hunks to Symbol or CodeRegion targets with confidence.

## Inputs

- old and new file content
- Git hunk ranges
- parser spans
- symbol table
- code-region candidates
- file path
- commit SHA

## Outputs

Mappings to Symbol, CodeRegion, or review-only unresolved mapping.

## Algorithm

```text
1. Parse old and new file snapshots.
2. Build symbol spans and stable names.
3. Intersect hunk old/new ranges with old/new spans.
4. Compare structural diff and text diff.
5. If one symbol contains the hunk in both snapshots, map to Symbol.
6. If hunk is sub-symbol or non-symbol config/JSX/CSS/docs, create or reuse CodeRegion.
7. If multiple entities overlap, emit review-only candidates.
```

## Confidence Scoring

Base `0.50` plus span containment `0.25`, old/new agreement `0.15`, signature stability `0.05`, no competing overlap `0.05`. Cap at `0.65` when parser is missing.

## Failure Modes

Parser failure maps file-level only. Multi-symbol hunks create multiple lower-confidence mappings. Formatting-only diffs can create structural-only mapping.

## Product Usage

Determines version updates, relation occurrences, and code evidence for cards.

## Real-Session Eval

Use AMO UI/code commits where JS/CSS/HTML changes should map to support CodeRegions without central atom flood.

## Worked Example

Input: hunk lines `45-67` in `auth.py`; old `login()` span `40-80`; new `login()` span `42-85`.

Intermediate: hunk fully inside same symbol, signature unchanged, no competing overlap.

Output: Symbol mapping `auth.py::login`, confidence `0.91`, reason `hunk falls within same symbol span in old and new parse`.
