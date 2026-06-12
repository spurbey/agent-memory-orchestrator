# Agent Tool Contract

## Purpose

Agents should call one generic harness tool instead of choosing among many narrow tools. The harness owns intent validation, correction, traversal recipe selection, and budget enforcement.

The canonical contract is [amo_harness_query](./contracts/amo_harness_query.md).

## Agent Responsibilities

The agent should provide:

- current user goal
- requested intent
- exact anchors when known
- recent tool result when asking for overlay
- budget and detail level
- session state for novelty filtering

## Harness Responsibilities

The harness must:

- validate or correct intent
- resolve anchors
- prefer exact and structural context before vector discovery
- return compact cards
- expose confidence and evidence
- suppress already-seen nodes, relations, and cards
- return safe failure statuses instead of speculative guidance

## Intent Set

- `edit_plan`: find likely files, risks, and tests before editing.
- `tool_overlay`: annotate recent rg/open/test output.
- `file_context`: explain one file or symbol in current repo context.
- `why_changed`: explain historical reason and version lineage.
- `impact_check`: identify related symbols/files/tests before or after an edit.
- `test_plan`: identify validation targets and prior validation evidence.

## Output Discipline

Cards must be short by default. Long narrative belongs only to `why_changed` or `detail=deep`.
