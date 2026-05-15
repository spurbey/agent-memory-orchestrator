# VALIDATED_BY And Test Linking

## Depends on
- decision-extraction.md
- relationship-extraction.md
- ../graph_model/edge-types.md

## Used by
- ../modules/session-graph-builder.md
- ../modules/central-graph-merge-engine.md

## Related docs
- ../examples/ndk-version-change.md
- ../graph_model/status-lifecycle.md

## Inputs

`DecisionThread`, `DecisionUnit` or `Fix`, `CodeNode`, and `TestRun` events with timeline order and result status.

## VALIDATED_BY Rule

Create `VALIDATED_BY` only when:

- a `TestRun` occurs after the relevant write/code node,
- the test is in the same `DecisionThread`,
- test result is pass/success,
- the decision/fix has a code or tool action to validate.

## Confidence Bump

If a planned decision at `0.60` is followed by tool-confirmed work and passing test, bump to `0.80`.

If a fix has code change plus passing test, bump to `0.90`.

Human explicit confirmation can set `1.00`.

## Failed Tests

Failed tests must not create `VALIDATED_BY`. They create `FAILED_VALIDATION` from test to decision/fix or `BLOCKED_BY` from work to failure.

## Pseudocode

```python
for test in thread.test_runs:
    target = nearest_prior_decision_or_fix_with_code_change(thread, test)
    if not target:
        continue
    if test.passed:
        create_edge(target, test, VALIDATED_BY)
        bump_confidence(target)
    else:
        create_edge(target, test, FAILED_VALIDATION)
```

## Tests

- Passing test after write creates `VALIDATED_BY`.
- Passing test before write does not validate later work.
- Failed test creates `FAILED_VALIDATION`.
- Confidence bump is applied exactly once.
