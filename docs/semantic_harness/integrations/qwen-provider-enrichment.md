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
- typed semantic facts with `fact_type`, `derivability`, and `source_refs`

Fact derivability must be explicit:

```text
derivable_from_current_code
derivable_from_docs
requires_git_history
requires_agent_session_history
requires_human_intent
requires_runtime_observation
mixed
unknown
```

Facts marked `requires_*` are the memory layer that can beat a current-code
baseline. Provider output that only restates the current diff should normally be
marked `derivable_from_current_code` and evaluated as a shortcut, not as product
proof.

## Review Outcomes

```text
accepted
review_only
rejected
quarantined
semantic_pending
```

## Source Quality

Trust depends on source class, derivability, and verification state. It is not
a flat provider confidence score.

```text
manual_annotation:
  highest trust when anchored and reviewable

human_commit / pull_request:
  high trust for non-derivable rationale when source-backed

agent_session:
  useful only from validated/committed or final-summary spans
  lower trust than human/manual facts by default

docs / docstrings:
  useful for semantic role and declared contracts
  stale-risk unless verified against current code

current_code:
  derivable shortcut, not hidden memory

imported_history:
  lower confidence unless source provenance is strong
```

Intermediate agent hypotheses are never accepted as graph truth. They may be
preserved for audit or review-only queues, but they must not power
`context_for_anchor`, relationship, history, or pre-edit answers.

## Quality Gate

Use 5-10 hand-picked commits with known human reasons. Accepted
`RelationOccurrence.reason` values must match the substantive human reason.
Generic reasons such as "modified the function" fail.

The quality gate must include at least one non-derivable reason:

```text
prior revert reason
failed earlier implementation
human design intent
production/runtime observation
agent-session rationale not visible in current code
```

Generic accepted facts are not allowed to unlock relationship/history
algorithms.
