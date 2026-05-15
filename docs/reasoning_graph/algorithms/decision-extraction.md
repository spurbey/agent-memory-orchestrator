# Decision Extraction

## Depends on
- chunking-and-decision-threads.md
- ../modules/qwen-contracts.md

## Used by
- relationship-extraction.md
- ../modules/session-graph-builder.md

## Related docs
- validated-by-and-test-linking.md
- ../graph_model/node-types.md

## Inputs

One `DecisionThread` with ordered user messages, assistant messages, tool events, code nodes, and tests.

## Outputs

`DecisionUnit`, `Bug`, `Fix`, and `OpenQuestion` nodes with confidence and evidence refs.

## Deterministic Patterns

`I will <action> because <reason>` -> planned action, confidence `0.60`.

`Fixed by <action>` -> completed fix, confidence `0.80` until tests pass.

`The issue is <cause>` -> investigation result, confidence `0.60`, or `0.80` if tool output confirms.

`Pinning <subject> to <value>` -> constraint, confidence `0.75`.

`Reverting <subject>` -> revert decision, confidence `0.85` when matching code node exists.

`Test passed after <change>` -> validation note, handled by `validated-by-and-test-linking.md`.

## Qwen Fallback

Use Qwen only when deterministic patterns do not extract a decision but the thread contains durable work signals. Qwen input and output must follow `modules/qwen-contracts.md`.

## Confidence Rules

```text
assistant stated plan              0.60
assistant stated + tool confirms   0.80
fix applied + test passed          0.90
human explicitly confirmed         1.00
```

## Pseudocode

```python
for message in thread.agent_messages:
    extracted = rule_extract(message)
    if extracted:
        create_decision(extracted)
if no_decision and durable_signals(thread):
    qwen_result = qwen_decision_extract(thread)
    if qwen_result.schema_valid and qwen_result.confidence >= 0.70:
        create_decision(qwen_result)
    else:
        create_review_candidate(qwen_result)
```

## Graph Effects

Creates `DecisionUnit`, `Bug`, `Fix`, or `OpenQuestion`; links to `DecisionThread`, `ExtractionRun`, and evidence.

## Tests

- Each deterministic phrase creates expected node type.
- Low-confidence Qwen output creates review candidate only.
- Invalid Qwen JSON creates diagnostic only.
- Decision node includes evidence and extraction run.
