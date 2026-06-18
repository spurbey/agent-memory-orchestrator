# Agent Semantic Checkpoint

## Purpose

The live repo-semantic producer is a forked same-context coding agent that writes
structured checkpoint JSON. AMO does not scrape raw chat and does not let the
agent write graph truth.

Flow:

```text
forked Codex session
-> semantic_checkpoint.json with human anchors
-> AMO parses contract
-> AMO resolves anchors against warmed Semantic Harness graph
-> AMO converts to SemanticFactProposal
-> deterministic review
-> pending review artifact
-> optional accepted-only graph attach
-> projection/cache refresh by existing semantic-fact projection path
```

## Skill Location

Repo-local skill:

```text
.codex/skills/amo-semantic-checkpoint/SKILL.md
```

User-level install/copy target:

```text
C:\Users\sumit\.codex\skills\amo-semantic-checkpoint\SKILL.md
```

The repo-local skill is the source of truth during development. Copy it to the
user-level path only when running a real forked Codex checkpoint eval.

## Trust Boundary

The forked agent may propose facts, but AMO owns:

```text
anchor resolution
graph node IDs
source-ref normalization
generic-fact rejection
intermediate-hypothesis rejection
accepted/review_only/rejected status
graph mutation
projection refresh
```

The forked agent must output paths, symbols, line ranges, and source excerpts.
It must not output graph node IDs unless AMO supplied an explicit allowed-node
catalog for that run.

## Checkpoint Boundary

The checkpoint covers work from `base_commit` or `session_start_commit` through
current `HEAD`, plus explicitly included uncommitted changes. The agent must not
infer future intent after the fork point.

## Default Ingest Mode

Default mode is pending:

```text
semantic_checkpoint.json
-> parse
-> resolve
-> review
-> write artifacts under .tmp/amo-semantic-checkpoints/<checkpoint_id>/
-> no graph mutation
```

Attach is separate and accepted-only:

```text
amo-cli semantic-checkpoint attach --review <review_artifact.json> --repo-id <repo_id>
```

Review-only facts stay in artifacts for v1 and are not projected into normal
retrieval.

## Manual First Eval

1. Complete or identify a real coding task with at least one commit.
2. Fork the same Codex session after the checkpoint boundary.
3. In the fork, use the `amo-semantic-checkpoint` skill.
4. Write `.tmp/amo-semantic-checkpoints/<checkpoint_id>/semantic_checkpoint.json`.
5. Run pending ingest.
6. Inspect `resolved_proposals.json`, `review_result.json`, `attach_plan.json`,
   and `comparison_report.md`.

Pass criteria:

```text
facts are anchored to real files/symbols/code regions
at least one useful non-generic fact is accepted or review_only
no invented anchors accepted
generic/mechanical facts rejected
intermediate hypotheses rejected or excluded
pending mode makes no graph mutation
attach mode only writes accepted facts
context_for_anchor can retrieve an attached accepted fact
```

## External Provider Policy

The external-provider packet path remains experimental backfill/eval only.
It is not the live producer, is not wired into MCP/live query, and does not
attach graph facts by default.
