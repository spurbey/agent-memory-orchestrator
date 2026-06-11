# False Positive Eval

## Purpose

Measure when harness context misleads the agent.

## Inputs

- historical task
- expected investigation path
- harness cards
- agent actions
- outcome labels

## Outputs

`mislead_rate`, `strict_card_precision`, and card failure reasons.

## Algorithm

```text
1. Label expected files, tests, and safe warnings for a session.
2. Replay harness query points.
3. Compare cards against expected path.
4. Mark misleading cards that point to the wrong area with confidence >= threshold.
5. Compute mislead_rate and precision.
```

## Confidence Scoring

A misleading card is severe when confidence is `>= 0.75` and priority is required or recommended.

## Failure Modes

Unlabeled sessions cannot measure `mislead_rate`. Ambiguous labels require review set.

## Product Usage

Blocks automatic sidecar activation until `mislead_rate <= 0.05`.

## Real-Session Eval

Run against both rich and partial AMO fixtures.

## Worked Example

Input: card recommends editing `billing.py` for signin task with confidence `0.81`, but labeled path is `auth/session.py` and `login_button.tsx`.

Intermediate: wrong area, high confidence, recommended priority.

Output: `misleading=true`, reason `wrong_area_high_confidence`.
