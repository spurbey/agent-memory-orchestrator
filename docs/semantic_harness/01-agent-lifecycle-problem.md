# Agent Lifecycle Problem

## Current Coding-Agent Loop

A coding agent normally works like this:

```text
read user task
-> search with rg or file listing
-> open files
-> infer code relationships from raw text
-> patch code
-> run tests
-> repeat until done
```

The raw tools return text, paths, and command output. They do not explain historical reasons, relation strength, active versions, prior validation, or task-specific risk.

## Why Agents Miss Things

Agents miss context because they have limited windows and no durable repo memory during tool use. Common failures:

- They inspect files by lexical match but miss the real dependency path.
- They overwrite or duplicate logic because they do not know why an existing function exists.
- They edit a symbol without seeing historically co-changed symbols.
- They skip tests that previously validated the same behavior.
- They trust a semantic match even when graph evidence is weak.
- They over-engineer because the repo structure and historical intent are not available together.

## What LSP And Static Tools Provide

LSP-style tools provide current structure:

- definitions
- references
- imports
- call sites
- types
- symbols
- diagnostics

This is necessary but incomplete. LSP does not know why a function changed across sessions, which commit introduced a relation, what agent discussion led to it, or which prior validation proved it.

## Harness Product Role

Semantic Harness adds:

- historical work causality
- version lineage
- relation occurrence evidence
- high-confidence traversal cards
- task-specific warnings
- test and impact guidance

It should sit beside agent tools as a local context service. The agent can call it explicitly first. Sidecar mode can annotate tool results automatically only after false-positive evals pass.
