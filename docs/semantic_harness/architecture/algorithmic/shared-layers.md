# Algorithmic Shared Layers

## Purpose

Define reusable layers under mode-specific algorithms. These layers make the
future implementation debuggable and prevent each mode from rebuilding its own
parser, traversal, scoring, and result-shaping machinery.

## Input Normalization

Each mode gets a normalized input object before planning:

```text
AnchoredQueryInput
ToolHitInput
PlannedEditInput
PatchDiffInput
HistoryInput
RelationshipInput
```

These objects contain paths, symbols, line ranges, recent tool output,
questions, already-seen state, and budget. They do not contain database
queries.

## Anchor Resolution

Anchor resolution is shared and conservative:

```text
exact path -> File
path + symbol -> Symbol
path + line -> smallest containing Symbol or CodeRegion
docs/config/markdown region -> File fallback with warning
unresolved/ambiguous -> partial or review_only, not accepted truth
```

## Feature Extraction

Feature extraction converts graph and retrieval evidence into numeric or
categorical signals:

```text
lexical_match
candidate_local_semantic_match
line_grounding
symbol_grounding
path_role
active_version
structural_proximity
edge_strength
cochange_count
occurrence_relevance
source_trust
derivability
validation_support
staleness
already_seen_penalty
noise_penalty
```

Feature code should be reusable and testable. It should not format product
answers.

## Candidate-Local Projection Search

Some modes need semantic similarity, but it must stay bounded by the mode's
candidate set.

For `rank_tool_hits`:

```text
raw tool output -> candidate files/lines/symbols
candidate graph objects -> attached projection docs
UserPromptSubmit + goal + search_terms -> query vector
candidate docs only -> similarity scores
```

This is different from global vector search. Global vector search may discover
candidate anchors for vague tasks, but `rank_tool_hits` already has candidates
from the tool output. It must not introduce unrelated files that the tool did
not return unless the caller explicitly asks for expansion.

Embedding backend policy:

```text
actual embedding model preferred
hash_token_char_cosine_v1 is degraded fallback only
backend and model id recorded in result/eval artifacts
unchanged projection docs reuse vectors by content_hash
changed projection docs re-embed
```

Projection documents eligible for candidate-local ranking:

```text
file summaries
symbol summaries
code-region summaries
accepted semantic facts
verified doc/docstring claims
relation occurrence summaries
work-window summaries
selected validation/test summaries
```

Do not embed raw AST floods or unreviewed provider speculation as product
ranking memory.

## Traversal

Traversal must be bounded, typed, and explainable:

```text
typed edge whitelist per mode
depth and node budget per mode
edge cost table per mode
node prize table per mode
occurrence filtering before answer generation
diversification before final output
```

Allowed algorithm families:

```text
weighted BFS / bounded expansion
PPR/RWR-style proximity for seed neighborhoods
Steiner-style compact connectors for multiple anchors
MMR/diversity selection for non-duplicate paths
task-specific occurrence filtering for co-change evidence
```

Disallowed:

```text
unbounded graph walks
LLM-generated arbitrary graph queries
vector-only answer truth
co-change-as-causality without reviewed reason
```

## Scoring

Scores should be explicit compositions, not opaque model outputs.

Example relation strength:

```text
stored_strength =
  0.45 * cochange_jaccard
+ 0.20 * recency_score
+ 0.20 * validation_score
+ 0.15 * reason_quality_score
```

Agent-facing historical relation output also requires:

```text
stored_strength >= configurable threshold
cochange_count >= configurable minimum, default 3
task-relevant occurrence unless structural fallback is explicitly allowed
```

## Result Shaping

Every mode has a typed result:

```text
RankToolHitsResult
ContextForAnchorResult
RelationshipResult
PreEditReviewResult
HistoryForAnchorResult
SemanticDiffResult
```

Legacy cards may wrap these results for compatibility, but mode logic must not
be written around cards.
