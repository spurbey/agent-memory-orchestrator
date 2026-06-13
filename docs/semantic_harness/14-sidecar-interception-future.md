# Sidecar Interception Future

## Purpose

Automatic sidecar annotation can eventually enrich raw tool results before the agent asks. It is not enabled in the first implementation.

## Architectural Constraints

Explicit query mode and sidecar mode must share the same planner.

Tool results must be representable as `recent_tool_result` in `amo_harness_query`.

Sidecar annotations must use the same HarnessCard contract.

Session state must suppress repeated nodes, relations, and cards.

## Activation Gate

Automatic injection is disabled until all are true:

```text
mislead_rate <= 0.05
strict_card_precision >= 0.85
agent-visible token overhead stays within budget
real-session eval passes on rich and partial fixtures
```

## Sidecar Output Rule

Sidecar mode may attach cards but must not block raw tool results. If card confidence is weak, it should attach no card and record an eval event.

Sidecar mode must not attach cards that only restate the visible tool result. The first implemented shadow rules are:

```text
file_read:
  suppress same-file-only cards such as "Inspect <file just opened>"
  attach only if extra graph context remains, such as docs, dependencies, history, or validation guidance

test_output:
  attach only when failing files, traceback lines, or assertion anchors are graph-grounded
  suppress successful output with no anchors

search:
  suppress broad search output when many files match and the only cards are exact-anchor next_file echoes
  attach only when the harness adds a stronger graph-grounded signal than the raw result list
  broad-search focus cards rank visible graph-grounded hits by path role and query-token overlap
  suppress broad search when every candidate has the same weak/no-focus score

git_diff / apply_patch:
  attach concise risk cards for graph-grounded changed files
  suppress duplicate cards already seen in the session

unknown / inventory:
  suppress path-only inventory/status output from git status, git branch, git ls-files, and Test-Path
```

These rules keep the sidecar append-only path conservative until real-session eval proves higher signal.
