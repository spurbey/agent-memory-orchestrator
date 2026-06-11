# Codex Skill Protocol

## Purpose

Teach Codex when and how to call Semantic Harness explicitly.

## Skill Guidance

Codex should call `amo_harness_query`:

- before editing unfamiliar code
- after broad `rg` output that returns many candidates
- when opening a file with unclear ownership or history
- before editing a symbol with likely broad impact
- when selecting tests for changed behavior

## Session State

Codex should pass already seen node, relation, and card IDs when available. This lets the harness suppress repeated context.

## Output Handling

Codex should treat required next actions as blocking unless the user explicitly redirects. Low-confidence cards should guide exploration, not direct edits.
