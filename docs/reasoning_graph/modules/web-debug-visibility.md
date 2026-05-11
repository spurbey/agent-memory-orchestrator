# Web Debug Visibility

## Depends on
- graph-validation.md
- ../architecture/04-data-flow-end-to-end.md

## Used by
- ../implementation/08-phase-web-debugging.md

## Related docs
- ../examples/contested-decision-flow.md
- ../examples/code-query-flow.md

## Purpose

Expose graph construction and validation state to the user so polluted or incomplete graph behavior is visible.

## Inputs

Daemon APIs for timeline, extraction runs, session graph, central merge, validation, contested nodes, and communities.

## Outputs

Web views and JSON payloads for inspection.

## Owned state

No graph truth. UI state only.

## Public interfaces planned

- Session timeline view.
- Extraction run comparison view.
- Session graph view.
- Central merge plan/result view.
- Contested decisions view.
- Community graph view.
- Failure diagnostics view.

## Kuzu writes

None.

## Failure modes

If daemon is unavailable, UI must say daemon unavailable. It must not display stale graph as live truth.

## Validation checks

Every graph view shows active `AMO_HOME`, graph path, extraction run id, and validation status.