# Budget And Novelty Selection

## Purpose

Return high-value cards without flooding the agent context.

## Inputs

- candidate cards
- max_cards
- max_tokens
- already_seen nodes/relations/cards
- status
- intent

## Outputs

Selected cards, suppressed cards, warnings.

## Algorithm

```text
1. Remove cards already seen unless status changed.
2. Rank by safety impact, confidence, task relevance, and novelty.
3. Reserve budget for required warnings and next actions.
4. Select cards until max_cards or max_tokens is reached.
5. Store suppressed reasons for eval.
```

## Confidence Scoring

Selection score equals confidence `0.35`, relevance `0.30`, safety impact `0.20`, and novelty `0.15`.

## Failure Modes

If all cards are duplicates, return no cards with a suppress warning. If only weak cards exist, return `low_confidence`.

## Product Usage

Keeps default harness output strict and compact.

## Real-Session Eval

Replay repeated `tool_overlay` calls and verify repeated cards are suppressed.

## Worked Example

Input: 8 candidate cards, `max_cards=3`, two already seen.

Intermediate ranking: risk card `0.88`, next_file card `0.82`, test_target card `0.76`, duplicate dependency card suppressed.

Output: 3 cards selected and duplicate dependency cards suppressed.
