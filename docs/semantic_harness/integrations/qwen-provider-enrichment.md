# Qwen / Provider Enrichment

## Purpose

Use a provider model to enrich graph facts after repo change events. This is a
cold-path enrichment stage, not live MCP query execution.

## Sources

```text
agent session commit
human commit
PR branch
merged PR
manual patch
imported git history
```

## Deterministic Facts First

Every change source first creates deterministic graph facts:

```text
Commit
WorkWindow
FileVersion
SymbolVersion
CodeRegionVersion
Hunk
CHANGED_IN
VERSION_OF
MAPS_TO_SYMBOL
MAPS_TO_CODE_REGION
CO_CHANGED_WITH semantic_pending occurrence
```

## Provider Proposals

The provider may propose:

- problem
- cause
- decision
- fix
- constraint
- validation meaning
- risk hints
- test hints
- `RelationOccurrence.reason`

## Review Outcomes

```text
accepted
review_only
rejected
quarantined
semantic_pending
```

## Source Quality

Confidence depends on source:

```text
agent_session: richest evidence
pull_request: strong when title/body/comments/CI exist
human_commit: commit/diff-limited
imported_history: weakest
```

## Quality Gate

Use 5-10 hand-picked commits with known human reasons. Accepted
`RelationOccurrence.reason` values must match the substantive human reason.
Generic reasons such as "modified the function" fail.
