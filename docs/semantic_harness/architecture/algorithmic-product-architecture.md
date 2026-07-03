# Algorithmic Product Architecture

## Purpose

Define the product direction after the question-driven reset. The next work is
not more generic cards. The next work is a small set of mode-specific
algorithms that help a coding agent decide:

```text
which search hits to open first
what an anchor means or constrains
how multiple anchors are related
what planned edits may break
why an anchor changed over time
whether an actual patch violates intended behavior
```

Each mode must have its own parser, scoring model, traversal policy, result
shape, and eval gate. Shared graph operations live below the modes; transport
and provider calls stay above or outside them.

## Product Spine

```text
agent tool/query context
-> mode request
-> anchor and input normalization
-> mode planner
-> graph/text/vector candidate operations
-> bounded traversal and scoring
-> typed result
-> agent next action
```

The harness should not duplicate raw `rg`, LSP, `git`, or file-read output.
Those tools expose facts from the repo. AMO should add ordering, linkage,
memory, risk, and reviewed semantic context.

## Architecture Documents

Read these in order:

1. [Mode design](./algorithmic/modes.md)
2. [Shared layers](./algorithmic/shared-layers.md)
3. [Execution order](./algorithmic/execution-order.md)

## Mode Families

```text
rank_tool_hits:
  rank broad search output so the agent opens the right files first

context_for_anchor:
  answer a specific semantic question about a known anchor

relationship_between_anchors:
  explain meaningful paths among multiple anchors

pre_edit_review:
  catch missed files, tests, risks, and constraints before patching

history_for_anchor:
  answer why and when an anchor changed

semantic_diff:
  review actual patch hunks against goal and graph constraints
```

## Data Readiness Gates

Algorithms must degrade honestly:

```text
structure only:
  rank hits, structural relationships, structural pre-edit warnings

reviewed semantic facts:
  rationale, constraints, risks, validation expectations

relation occurrences with accepted reasons:
  historical relation explanations and task-specific co-change meaning

embeddings:
  candidate discovery over summaries, facts, reasons, docs, and cards

proxy delivery:
  only after MCP mode outputs beat no-AMO baseline
```

## What Stays Frozen

Do not extend these as product architecture:

```text
domain/semantic_harness/query.py
application/services/semantic_harness/tool_context/search_focus.py
generic card generation
shadow-only attach/suppress tuning
external provider live prompt path
```

Allowed work there:

```text
bug fixes
compatibility repairs
eval evidence preservation
migration adapters
```

## What To Avoid

Avoid building:

```text
new product behavior in legacy cards
provider calls inside live MCP query paths
mode logic inside MCP transport
mode logic inside SQLite/Helix adapters
direct HelixDB dependency in domain modes
large all-purpose query planner modules
LLM-owned traversal decisions
```

Do not delete probe artifacts yet. Keep them until mode-specific replacements
pass baseline evals, then retire or migrate them deliberately.
