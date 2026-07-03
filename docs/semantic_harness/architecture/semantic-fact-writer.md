# Semantic Fact Writer

## Purpose

Define how Semantic Harness turns existing evidence into typed semantic facts
that `context_for_anchor`, `relationship_between_anchors`, `history_for_anchor`,
and `pre_edit_review` can safely use.

This writer is a cold-path enrichment pipeline. It must not run inside live MCP
query handling.

## Boundary

```text
deterministic layer:
  owns structure, diffs, hunks, versions, doc links, co-change facts

provider/Qwen layer:
  proposes meaning, rationale, risks, invariants, relationship reasons

deterministic review:
  accepts, review-onlys, rejects, or quarantines proposals

attach layer:
  writes accepted typed facts to graph node metadata
```

Qwen/provider output is never graph truth by itself.

## Source Classes

### Repo Docs, Docstrings, And Comments

Input:

```text
README/docs markdown
docstrings
nearby code comments
config comments
```

Deterministic extraction:

```text
doc section -> mentioned files/symbols
docstring -> owning function/class
nearby comment -> code region
```

Provider/Qwen may propose:

```text
semantic_role
invariant_or_contract
usage_constraint
runtime_behavior
validation_expectation
docs_alignment
```

Review rule:

```text
unverified doc-derived facts are review_only
verified doc-derived facts must include verified_against_commit
doc facts rank below commit/session/manual facts for risk and invariant queries
```

Docs are claims. They can be stale.

### Human Commit Or PR

Input:

```text
commit message
diff hunks
changed files/symbols
PR title/body/comments
tests changed or CI signal
```

Deterministic extraction:

```text
commit -> hunks -> files/symbols/code regions
co-change occurrences
test links
version updates
```

Provider/Qwen may propose:

```text
implementation_rationale
historical_change
risk_or_impact
failure_mode
validation
relationship_reason
```

Review rule:

```text
human commit/PR non-derivable facts are high-trust when concrete and source-backed
generic reasons such as "updated the function" are rejected
```

### Agent Work Window

Input:

```text
user prompt
agent visible reasoning/status updates
tool calls/results
patch/diff
validation output
commit message
final summary
```

Deterministic extraction:

```text
tool result -> opened files
patch -> hunks
test output -> validation refs
commit -> work window
```

Provider/Qwen may propose:

```text
problem
cause
decision
fix
rejected approach
risk discovered
validation meaning
future harness hint
non-derivable rationale
```

Review rule:

```text
extract only validated/committed or final-summary spans
reject intermediate hypotheses as graph truth
session-derived facts rank below human commit/manual facts by default
```

Agent sessions contain wrong intermediate reasoning. Capturing that as accepted
memory would poison future sessions.

### Future Manual Annotation

Manual notes should use the same writer pipeline:

```text
/amo-note file=snapshots.py line=41
"Do not merge raw and unique edge counts; raw is observation-level."
```

Target fact:

```text
source_kind = manual_annotation
source_span = manual_note
derivability = requires_human_intent
review_status = accepted or review_only
```

Do not create a separate graph path for manual notes.

## Fact Scope

Facts may be:

```text
anchor_local:
  about one file, symbol, or code region

relationship:
  about two or more files/symbols/regions or a RelationOccurrence

system:
  about an architectural boundary or global rule
```

The writer supports all three. Current `context_for_anchor` can consume facts
attached to its requested anchors. Future relationship and pre-edit modes should
consume relationship and system facts directly.

## Review Outcomes

```text
accepted:
  may be attached to graph nodes and used in product answers

review_only:
  preserved for audit/eval, not used as strong answer evidence

rejected:
  unsupported, generic, malformed, stale-risk, or unvalidated hypothesis

quarantined:
  unsafe or structurally invalid provider output

semantic_pending:
  deterministic graph fact exists but semantic meaning is not reviewed yet
```

## Trust And Derivability

Every fact carries two separate dimensions:

```text
derivability:
  could the agent recover this from current code/docs, or does it require
  git history, session history, human intent, or runtime observation?

source trust:
  how reliable is the source class after deterministic review?
```

Trust order for conflicting accepted facts:

```text
manual_annotation
human_commit / pull_request non-derivable facts
validated agent_session non-derivable facts
verified docs/docstrings
current-code derivable facts
unverified docs/docstrings
imported or unknown history
```

This prevents a high-confidence provider summary from outranking a lower-score
human commit fact when both answer the same risk or history question.

## Verification And Staleness

Doc-derived and docstring-derived facts must include:

```text
verification_status
verified_against_commit
```

If a doc fact is unverified, it remains `review_only`. If it was verified
against an older commit and touched code has moved since then, the future update
pipeline must mark it `stale_risk` before product answers can treat it as
strong evidence.

## Current Code Placement

```text
domain/semantic_harness/semantic_facts/
  models.py      # canonical fact/proposal/source/trust contracts
  review.py      # deterministic proposal review
  attach.py      # accepted fact -> graph node metadata

domain/semantic_harness/query_modes/semantic_facts.py
  query-side reader for node.metadata["semantic_facts"]
```

Provider-specific calls belong in infrastructure/application later. The domain
writer package is provider-agnostic.
