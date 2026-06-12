# Rename Move Lineage

## Purpose

Detect when files, symbols, or code regions continue across rename, move, split, or merge operations.

## Inputs

- Git rename metadata
- old/new symbol tables
- body hashes
- signatures
- call/import neighborhoods
- hunk mappings

## Outputs

`RENAMED_TO`, `MOVED_TO`, `SPLIT_INTO`, `MERGED_INTO` edges or review-only candidates.

## Algorithm

```text
1. Use Git rename metadata for files.
2. Match symbols by qualified name and signature.
3. For changed names, compare body similarity and neighborhood similarity.
4. Detect split/merge by content distribution across targets.
5. Accept high-confidence lineage; send ambiguous cases to review.
```

## Confidence Scoring

Git file rename `0.95`. Same signature and high body similarity `0.90`. Body similarity plus neighborhood match `0.80`. Split/merge without clear distribution is review-only at `<= 0.65`.

## Failure Modes

Large rewrites become review-only. Generated files are excluded. Parser-missing languages use file-level lineage only.

## Product Usage

Allows `why_changed` and history queries to traverse through renamed or moved code.

## Real-Session Eval

Use a real or synthetic commit where a helper is moved between files and verify history remains connected.

## Worked Example

Input: `get_user` moved from `users.py` to `repository.py`, body similarity `0.88`, signature unchanged, callers updated.

Intermediate score: body `0.40`, signature `0.25`, neighborhood `0.21`, total `0.86`.

Output: `MOVED_TO` edge confidence `0.86`.
