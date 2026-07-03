# Algorithm: Context For Anchor

## Purpose

Answer a specific semantic question about a known file, symbol, or code region.
This mode is not a file profile and not an LSP replacement.

## Inputs

- goal
- anchors
- questions
- constraints
- budget

## Algorithm

```text
1. Resolve exact anchors.
2. Classify each question.
3. Map question type to typed semantic fact facets.
4. Filter facts by anchor and facet before lexical/vector ranking.
5. Prefer non-derivable accepted facts for why/risk/history questions.
6. Retrieve graph-grounded evidence.
7. Suppress raw dependency dumps unless explicitly requested.
8. Return semantic-first answer snippets and selective action links.
9. Recommend a deeper mode if the question exceeds local context.
```

## Question Routes

```text
semantic_role -> summaries, docs, docstrings, accepted frames
invariant -> constraints, tests, accepted decisions, docstrings
validation -> validation edges, test files, validation frames
risk -> action-relevant callers/importers, co-change, risk hints, API/persistence role
local_relation -> bounded path between anchor and mentioned entity
history -> versions, commits, work windows, reviewed frames
usage -> selective callers/importers only when explicitly asked
```

## Typed Fact Facets

`context_for_anchor` answers from typed facts, not raw node summaries alone.

```text
semantic_role
invariant_or_contract
implementation_rationale
historical_change
version_lineage
relationship_reason
risk_or_impact
validation
usage
data_model_or_storage
runtime_behavior
failure_mode
performance_or_scaling
docs_alignment
```

Each fact must carry:

```text
fact_type
anchor_node_ids
text
source_refs
confidence
review_status
derivability
discovery_cost
source_kind
```

`derivability` answers whether a smart coding agent could find the fact by
reading current code:

```text
derivable_from_current_code -> useful shortcut
derivable_from_docs -> useful shortcut
requires_git_history -> product-value memory
requires_agent_session_history -> product-value memory
requires_human_intent -> product-value memory
requires_runtime_observation -> product-value memory
mixed -> product-value memory when any required source is non-derivable
unknown -> do not treat as strong evidence
```

For `risk`, `history`, and implementation-rationale questions, non-derivable
accepted facts rank above derivable facts even when their confidence is slightly
lower. This prevents AMO from optimizing only for facts the baseline agent can
read from the current source tree.

## Output

```json
{
  "answers": [
    {
      "question": "what invariant does this maintain?",
      "answer": "Duplicate raw observations should not change structural identity.",
      "confidence": 0.78,
      "fact_type": "invariant_or_contract",
      "derivability": "derivable_from_current_code",
      "review_status": "accepted",
      "discovery_cost": "medium",
      "evidence": []
    }
  ],
  "invariants": [],
  "action_relevant_links": [],
  "recommended_next_mode": null
}
```

## Rules

- Do not dump all imports, callers, dependencies, or same-directory files.
- Include structural links only when they answer the question.
- Return `partial_structural` when only structure exists.
- Return `partial_historical` when history exists but current structure is weak.
- Return `clarification_needed` when no useful question is supplied.

## Phase Gate

The eval must use semantic misunderstanding fixtures, not navigation-only tasks.
The mode passes only if it prevents wrong edits or reduces discovery work versus
a no-AMO baseline.

The edge-count fixture is a plumbing and shortcut fixture because the answer is
derivable from current code. Product-value fixtures must include at least one
non-derivable accepted fact, such as a revert reason, hidden production
constraint, prior failed edit, or user/agent rationale not visible in the
current implementation.
