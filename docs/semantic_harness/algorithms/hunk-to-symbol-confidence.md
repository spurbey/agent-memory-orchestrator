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
1. Read Git hunks with zero context or derive changed-line ranges from the patch.
2. Parse old and new file snapshots.
3. Build symbol spans and stable names.
4. Intersect changed-line old/new ranges with old/new spans.
5. Compare structural diff and text diff.
6. If one symbol contains the changed lines in both snapshots, map to Symbol.
7. If changed lines are sub-symbol or non-symbol config/JSX/CSS/docs, create or reuse CodeRegion.
8. If multiple entities overlap the changed lines, emit review-only candidates.
```

Mapping must not use wide context hunks. Broad context is useful for Qwen work-causality packets, but it is unsafe as graph mutation input because one small edit can overlap many nearby symbols and suppress version/relation updates.

## Confidence Scoring

Base `0.50` plus span containment `0.25`, old/new agreement `0.15`, signature stability `0.05`, no competing overlap `0.05`. Cap at `0.65` when parser is missing.

## Failure Modes

Parser failure maps file-level only. Multi-symbol changed-line hunks create multiple lower-confidence mappings. Formatting-only diffs can create structural-only mapping. If only a wide-context diff is available, first reduce it to changed-line ranges before mapping.

## Product Usage

Determines version updates, relation occurrences, and code evidence for cards.

## Real-Session Eval

Use AMO UI/code commits where JS/CSS/HTML changes should map to support CodeRegions without central atom flood.

## Worked Example

Input: hunk lines `45-67` in `auth.py`; old `login()` span `40-80`; new `login()` span `42-85`.

Intermediate: hunk fully inside same symbol, signature unchanged, no competing overlap.

Output: Symbol mapping `auth.py::login`, confidence `0.91`, reason `hunk falls within same symbol span in old and new parse`.

Counterexample: a one-line edit inside `login()` is displayed with 80 lines of Git context and the context also includes `logout()` and `refresh()`. The mapper must score only the changed line, not the full displayed context. Otherwise the hunk becomes a false multi-symbol overlap and version/co-change edges are incorrectly withheld.
