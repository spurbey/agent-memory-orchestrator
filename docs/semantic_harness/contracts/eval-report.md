# Contract: Eval Report

## Shape

```json
{
  "eval_id": "string",
  "fixture": "string",
  "query": {},
  "baseline_raw_tools": {},
  "baseline_amo_retrieval": {},
  "harness_result": {},
  "expected": {},
  "metrics": {
    "strict_card_precision": 0.0,
    "next_file_hit_rate_top3": 0.0,
    "test_selection_hit_rate": 0.0,
    "mislead_rate": 0.0,
    "idempotent_replay_rate": 1.0
  },
  "passed": true,
  "failure_reason": ""
}
```

## Required Evidence

Each report must include exact fixture ID, query input, returned cards, expected cards or actions, and pass/fail reason.
