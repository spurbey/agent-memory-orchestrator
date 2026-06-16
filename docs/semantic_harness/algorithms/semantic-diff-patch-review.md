# Algorithm: Semantic Diff Patch Review

## Purpose

Review an actual patch against the intended goal, planned edits, and graph
invariants.

## Inputs

- goal
- planned edits
- actual diff
- hunk-to-symbol mappings
- graph evidence

## Algorithm

```text
1. Parse diff.
2. Map hunks to File, Symbol, and CodeRegion nodes.
3. Compare planned edits with actual changed entities.
4. Re-run pre_edit_review on actual changed set.
5. Detect unexpected files, symbols, and semantic drift.
6. Return patch risks and tests to run.
```

## Output

```json
{
  "changed_entities": [],
  "unexpected_changes": [],
  "patch_risks": [],
  "tests_to_run": []
}
```

## Readiness

This mode is reserved until `pre_edit_review` and hunk mapping are stable.
