# Budget And Novelty Selection

## Purpose

Return high-value cards without flooding the agent context.

## Inputs

- candidate cards
- max_cards
- max_tokens
- already_seen nodes/relations/cards
- intent

## Outputs

Selected cards, suppressed cards, warnings.

## Algorithm

```text
1. Score each candidate by route priority, card type priority, confidence, and evidence density.
2. Sort by descending score, then deterministic card-type rank, title, and card_id.
3. Suppress cards whose card_id is already in session_state.already_seen_card_ids.
4. Suppress cards whose support nodes are already in session_state.already_seen_node_ids.
5. Suppress cards whose support nodes are fully covered by already selected cards.
6. Select cards until max_cards or max_tokens is reached.
7. Store every suppressed reason for eval/debug.
```

## Confidence Scoring

Selection score:

```text
route_priority * 0.45
+ card_type_priority * 0.25
+ confidence * 0.22
+ evidence_density * 0.08
```

Route priority:

```text
exact anchor card = 1.00
doc_support = 0.82
historical_relation = 0.72
dependency = 0.68
fallback structural = 0.60
lexical projection = 0.50
vector projection = 0.38
```

Card type priority:

```text
risk = 1.00
test_target = 0.92
next_file = 0.88
symbol_context = 0.82
historical_relation = 0.78
dependency = 0.74
doc_support = 0.70
unknown = 0.50
```

Evidence density is capped at `1.0` with `len(card.evidence) / 4`.

This scoring intentionally makes exact anchors and deterministic graph support stronger than lexical/vector discovery. A vector candidate can be returned when it is the only grounded candidate, but it should not displace a direct file or symbol anchor.

## Failure Modes

If all cards are duplicates, return no selected cards and retain `already_seen_card`, `already_seen_nodes`, or `duplicate_selected_nodes` suppressed reasons for eval.

If token budget is too small, keep at least the first selected card unless `max_cards=0`. This avoids an empty response caused only by rough token estimation.

If only weak lexical/vector candidates exist and graph grounding fails upstream, no card reaches this selector; the query response should be `low_confidence` or `unavailable` depending on the retrieval route.

## Product Usage

Keeps default harness output strict and compact.

## Real-Session Eval

Replay repeated `tool_overlay` calls and verify repeated cards are suppressed.

## Worked Example

Input:

```text
max_cards = 2
already_seen_card_ids = ["c:old"]
already_seen_node_ids = ["file:repo:src/old.py"]

Candidate A:
  card_id = c:exact
  type = next_file
  route = exact anchor
  confidence = 0.72
  evidence_count = 1

Candidate B:
  card_id = c:vector
  type = symbol_context
  route = vector projection
  confidence = 0.62
  evidence_count = 2

Candidate C:
  card_id = c:old
  type = next_file
  route = exact anchor
  confidence = 0.80
  evidence_count = 1
```

Intermediate:

```text
Candidate A score =
  1.00 * 0.45 + 0.88 * 0.25 + 0.72 * 0.22 + 0.25 * 0.08
  = 0.8484

Candidate B score =
  0.38 * 0.45 + 0.82 * 0.25 + 0.62 * 0.22 + 0.50 * 0.08
  = 0.5524

Candidate C suppressed = already_seen_card
```

Output:

```text
selected = [c:exact, c:vector]
suppressed = [{card_id: c:old, reason: already_seen_card}]
```
