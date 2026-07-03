# Agent Tool Contract

## Purpose

Agents should call one generic harness tool instead of choosing among many narrow tools. The harness owns intent validation, correction, traversal recipe selection, and budget enforcement.

The canonical contract is [amo_harness_query](./contracts/amo_harness_query.md).

## Current Surface

The first model-visible surface is the MCP tool `amo_harness_query`.

Rules:

- the repo graph must already be warmed with `amo-harness bootstrap`
- the MCP tool must not bootstrap, mutate, or inject context automatically
- missing graph returns `status=unavailable` with `requires_bootstrap=true`
- automatic sidecar/proxy injection remains disabled until eval gates pass

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

## Tool Overlay Discipline

`tool_overlay` must add new graph-grounded context, not echo the raw tool result.

Attach only when the card adds one of:

- a graph-grounded file or symbol the agent has not already inspected
- documented support or constraints
- dependency, impact, or validation context
- high-confidence patch, diff, or failing-test guidance

Suppress when the overlay would only repeat the current tool result:

- file read already opened the exact file and the only card is `Inspect <same file>`
- broad search output has many matched files and the only cards are exact-anchor `next_file` echoes
- inventory/status output such as `git status`, `git branch`, `git ls-files`, or `Test-Path` only prints paths
- successful test output has no failing file, traceback, or assertion anchor
- card evidence is vector-only or otherwise not graph-grounded

This keeps sidecar context useful under token budget and prevents model-visible noise.
