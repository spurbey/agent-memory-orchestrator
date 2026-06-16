# MCP To Proxy Delivery Plan

## Purpose

Separate product intelligence from delivery. MCP proves usefulness first; proxy
delivery comes after quality gates pass.

## MCP Phase

MCP is explicit:

```text
agent decides to call amo_harness_query
agent sends mode, goal, anchors, questions, and budget
AMO returns mode-specific output
```

MCP is used for:

- context questions
- pre-edit checks
- relationship/history questions
- controlled evals

## Proxy Phase

Proxy is automatic delivery:

```text
native tool result
-> proxy sees result
-> AMO appends mode-specific overlay
-> native output remains recoverable
```

First proxy use case:

```text
append rank_tool_hits after rg output
```

## Replacement Policy

Replacement is disabled until:

- raw output has stable `raw_ref`
- lossless recovery works
- precision and mislead gates pass
- exit code and failure state are preserved
- proxy routing is stable

Initial proxy behavior is append-only.
