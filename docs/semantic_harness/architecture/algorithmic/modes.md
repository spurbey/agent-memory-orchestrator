# Algorithmic Mode Design

## Purpose

Define the mode-specific algorithms that replace generic cards as the product
shape.

## `rank_tool_hits`

Job:

```text
Turn broad search output into ranked file/line/symbol groups.
```

Algorithm:

```text
parse rows
-> group by file
-> map file:line to File/Symbol/CodeRegion
-> collect projection docs attached to candidate files/symbols/edges
-> compute candidate-local similarity from UserPromptSubmit + goal + search terms
-> score lexical, path-role, line-grounding, symbol match, active version,
   structural proximity, candidate-local semantic similarity, historical signal,
   already-seen state, and noise
-> diversify top groups
-> return rank-only output
```

Output must not be cards. It should be a compact ranked list with scores and
reason codes. This mode is structural-first and does not require Qwen.

Similarity search is not global discovery in this mode. The raw tool output
defines the candidate files first; embeddings only rank projection documents
attached to those candidates. The latest captured `UserPromptSubmit` is an input
signal because it usually contains the original problem statement that the
agent's later `rg` command compresses into a short search term.

Default score:

```text
0.20 rg_match_strength
0.20 line_symbol_grounding
0.20 graph_proximity_to_anchors
0.15 candidate_semantic_similarity
0.10 historical_signal
0.10 path_role_prior
0.05 validation_or_test_relevance
minus penalties
```

Use now:

```text
MCP explicit testing after search output
```

Use later:

```text
proxy ranked-first append after rg/grep output with raw_ref recovery
```

## `context_for_anchor`

Job:

```text
Answer a specific semantic question about a known file, symbol, or code region.
```

Algorithm:

```text
resolve anchors
-> classify question type
-> choose only the routes needed by that question
-> prefer accepted non-derivable facts for why/risk/history questions
-> include selective structural links only when asked or action-relevant
```

This is the current reliable product mode. It should remain question-driven and
semantic-first.

## `relationship_between_anchors`

Job:

```text
Explain meaningful relationships among multiple files, symbols, classes,
commits, tests, or code regions.
```

Algorithm family:

```text
resolve anchors
-> assign seed prizes
-> assign edge costs
-> bounded weighted expansion
-> PPR/RWR-style proximity scoring
-> Steiner-style compact connector candidates
-> path coherence scoring
-> gap-driven second expansion for weakly connected anchors
-> MMR-style path diversification
```

Readiness levels:

```text
v1 structural:
  CONTAINS, DEFINES, IMPORTS, CALLS, VALIDATED_BY, CHANGED_IN,
  CO_CHANGED_WITH with minimum occurrence gates

v2 reviewed semantic:
  accepted RelationOccurrence reasons, semantic facts, validated decisions

v3 enriched:
  embeddings over semantic facts/reasons/summaries and source-quality ranking
```

When semantic data is absent, return `partial_structural`; do not invent a
reason for a relationship.

## `pre_edit_review`

Job:

```text
Before patching, catch missed files, hidden coupling, risky assumptions,
affected tests, and semantic constraints.
```

Algorithm:

```text
ground planned edits
-> build action-relevant frontier
-> score structural risk
-> add accepted semantic constraints when available
-> identify must-inspect files and tests
-> return go, edit_with_warnings, or blocked
```

Risk features:

```text
fan-in and import/call role
test/validation criticality
public API, persistence, config, protocol, or schema role
co-change strength with cochange_count gate
accepted risk_or_impact facts
stale or missing semantic evidence
weak hunk/anchor mapping
```

Structural risk can warn. Semantic risk requires accepted semantic evidence.

## `history_for_anchor`

Job:

```text
Answer why and when an anchor changed.
```

Algorithm:

```text
anchor
-> active entity
-> versions
-> commits
-> work windows
-> reviewed semantic facts / relation occurrences / validation refs
-> timeline ranked by task relevance and source quality
```

It should cite versions, commits, work windows, and source refs. Imported or
weak history must be labeled lower confidence.

## `semantic_diff`

Job:

```text
After a patch exists, compare actual edits against intended goal and graph
constraints.
```

Algorithm:

```text
parse diff
-> map hunks to symbols/regions
-> compare planned edits vs actual edits
-> run pre_edit_review on changed set
-> report unplanned affected nodes, stale facts, tests, and semantic drift
```

This mode comes after planned-edit review and commit update are stable.
