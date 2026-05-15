# Example: Revert Flow

## Depends on
- ../algorithms/code-node-versioning.md
- ../algorithms/same-file-resolution.md

## Used by

## Related docs
- ../graph_model/status-lifecycle.md
- ../graph_model/central-versioning-rules.md

## Scenario

Session A changes `retry.py` from fixed delay to exponential backoff. Session B reverts backoff because it breaks rate limits.

## Session A

```text
DecisionUnit A: Use exponential backoff for retry stability.
CodeNode A: retry delay block with backoff().
```

## Session B

```text
DecisionUnit B: Revert exponential backoff because rate limits fail.
CodeNode B: retry delay block restored to fixed delay.
```

## Version Chain

```text
DecisionUnit B REVERTS DecisionUnit A
CodeNode B REVERTS CodeNode A
DecisionUnit A status -> superseded
CodeNode A status -> superseded
```

## Preservation Rule

Session A nodes remain in central graph with original evidence and commit links. Revert adds new knowledge; it does not erase history.
