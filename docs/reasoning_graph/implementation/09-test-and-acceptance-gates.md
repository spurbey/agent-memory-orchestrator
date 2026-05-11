# Test And Acceptance Gates

## Depends on
- ../modules/graph-validation.md
- 07-phase-fresh-rebuild.md
- 08-phase-web-debugging.md

## Used by
- ../README.md
- ../../IMPLEMENTATION_TRACKER.md

## Related docs
- ../architecture/05-failure-and-safety-model.md
- ../modules/qwen-contracts.md

## Goal

Define when the documentation and later implementation are acceptable.

## Modules touched

All modules through test and validation only.

## Inputs

Unit tests, integration tests, real AMO evidence, real Codex transcripts, real Git commits.

## Outputs

Pass/fail report.

## Algorithms used

All documented algorithms through their phase tests.

## Kuzu writes

Only test graph paths unless running accepted rebuild.

## CLI/API surface

`graph-validate-session`, `graph-validate-central`, `graph-contested`, `graph-version-flow`, `graph-why-file`.

## Unit tests

Every algorithm doc must have unit coverage.

## Real-data tests

At least one real coding session must pass full raw timeline -> session graph -> central merge -> clustering -> web inspection.

Same-file resolution has a stricter gate than other algorithms. It must be validated with real Codex session evidence where the same file is edited in multiple timeline segments. Synthetic fixtures are allowed for unit tests only. Replayed commit sequences without the session timeline do not satisfy the real-data gate.

Contested propagation should prefer a natural contested case from the real central graph. If the project has not accumulated a natural contested case by V1 acceptance time, use a controlled test graph path seeded from real extracted central nodes. This is an accepted V1 gap only if documented in the final validation report with:

- the reason no natural contested case was available
- the real nodes used as the seed
- the synthetic conflict edge added for validation
- a follow-up requirement to revalidate contested propagation after enough real sessions accumulate

This fallback cannot be used to claim natural contested-case coverage.

## Pass/fail criteria

No phase passes with generic-only graph nodes. No Qwen low-confidence output mutates graph. No raw/support node promotes to answer-grade. Fresh graph swaps only after validation.

Additional hard gates:

- daemon crash during a build/finalize/rebuild job must leave durable diagnostics and must not duplicate selected extraction runs
- deterministic community labels must be stable across repeated runs and must not be dominated by hashes, raw ids, or generic graph terms
- same-file resolution real-data gate cannot be waived by synthetic or commit-only fixtures
- incomplete candidate rebuild graph cannot swap active graph
- contested natural-data gap, if present, must be explicit in CLI/API/web validation output

## Must not do

Do not mark implementation complete based only on synthetic fixtures.

Do not hide known real-data coverage gaps behind passing unit tests.
