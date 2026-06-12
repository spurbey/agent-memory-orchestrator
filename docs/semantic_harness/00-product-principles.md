# Product Principles

## Purpose

Semantic Harness gives coding agents compact, high-confidence context while they work. It should reduce blind repo exploration, prevent unsafe edits, and explain why code relationships matter.

## Non-Negotiables

The harness is not a better retrieval UI.

The harness is a runtime context system for coding agents.

The graph optimizes for these questions:

- What should the agent inspect next?
- What should the agent avoid editing blindly?
- What tests matter?
- What historical reason explains a file, symbol, or dependency relation?
- What version is currently active?
- What prior work changed this function, region, or file?

## Default Output

Default output is strict and compact. The agent should receive cards that fit into an active coding context, not broad essays.

Long explanations are opt-in through `detail=deep` or the `why_changed` intent.

## Truth Boundary

Deterministic sources own graph truth:

- Git commits and hunks
- Static parsing and symbol spans
- Work-window boundaries
- Deterministic review gates
- Stored graph lineage and provenance

Qwen and vector retrieval propose candidates. They do not write truth directly.

## One Logical Graph

The target product has one logical repo knowledge graph. Storage can be split across Kuzu, SQLite, raw JSONL, FAISS, and stage artifacts, but identity and traversal must behave as one graph.

## Agent-Safety Bias

When confidence is weak, the harness returns `low_confidence` or `unavailable` instead of over-explaining. A wrong confident card is more harmful than no card.
