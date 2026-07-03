# Query Mode System

## Purpose

Define the target product shape for Semantic Harness queries. One MCP tool is
kept, but behavior is selected by `mode`.

## Public Tool

The public tool remains:

```text
amo_harness_query
```

The old `intent` field is accepted as a compatibility alias. New callers should
send `mode`.

## Modes

```text
context_for_anchor
rank_tool_hits
pre_edit_review
relationship_between_anchors
history_for_anchor
semantic_diff
```

## Responsibility Split

The coding agent owns:

- mode selection
- goal and search terms
- exact anchors already found
- questions it wants answered
- recent tool output
- planned edits
- budget and detail level

AMO owns:

- anchor validation
- question classification
- query expansion against graph/index vocabulary
- graph grounding
- traversal policy
- ranking and suppression
- output shaping
- compatibility fallback

The coding agent must not send arbitrary database traversal. AMO executes safe,
bounded query plans.

## Mode Output Principles

Each mode has a different output shape:

- ranking modes return ranked groups
- context modes return answer snippets and evidence
- review modes return risks, missing checks, and decision status
- relationship modes return paths and weak-link diagnostics
- history modes return timelines and source quality

Generic cards are compatibility output only.

## Failure Statuses

```text
ready
partial_structural
partial_historical
partial_coverage
low_confidence
clarification_needed
unavailable
```

`clarification_needed` is used when a question-driven mode receives no useful
question or receives a question that is too broad for the budget.
