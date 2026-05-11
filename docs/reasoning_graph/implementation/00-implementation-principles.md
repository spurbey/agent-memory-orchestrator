# Implementation Principles

## Depends on
- ../README.md
- ../architecture/03-runtime-ownership.md
- ../architecture/05-failure-and-safety-model.md

## Used by
- 01-phase-raw-timeline.md
- 09-test-and-acceptance-gates.md

## Related docs
- ../modules/kuzu-graph-store.md
- ../modules/qwen-contracts.md

## Goal

Establish rules every implementation phase must follow.

## Principles

Hooks capture only. Daemon owns heavy work. Kuzu is graph truth. Raw evidence is immutable. Extraction runs are versioned. Qwen cannot mutate graph unless schema and confidence gates pass. Central graph never deletes historical knowledge.

## Inputs

Accepted documentation specs and current repository state.

## Outputs

Implementation tasks that preserve compatibility shims and keep modules manageable.

## Algorithms used

None directly.

## Kuzu writes

None in this phase.

## CLI/API surface

None.

## Unit tests

N/A.

## Real-data tests

N/A.

## Pass/fail criteria

No code phase starts until docs are accepted.

## Must not do

Do not rebuild Kuzu, refactor code, or change runtime behavior during documentation acceptance.