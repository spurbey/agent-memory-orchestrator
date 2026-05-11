# Chunking And Decision Threads

## Depends on
- ../architecture/04-data-flow-end-to-end.md
- semantic-drift-detection.md

## Used by
- ../modules/session-graph-builder.md
- ../implementation/02-phase-session-graph.md

## Related docs
- same-file-resolution.md
- decision-extraction.md
- ../graph_model/node-types.md

## Inputs

Ordered timeline events for one session:

```json
[{"event_id":"event:1","kind":"AgentMessage","text":"Now let me check build.gradle.kts","files":["build.gradle.kts"],"index":12}]
```

## Outputs

`DecisionThread` nodes and chunk membership metadata. Each thread has topic label, file set, event ids, start/end index, and confidence.

## Boundary Signals

1. File switch: current meaningful file set differs from previous active file set.
2. Explicit transition phrase: assistant says phrases like `now let me`, `moving on`, `next issue`, `that is fixed`, `actually let me check`.
3. Semantic drift: rolling window similarity below `0.65` as specified in `semantic-drift-detection.md`.

## Pseudocode

```python
chunks = []
current = new_chunk(first_event)
for event in ordered_events:
    if is_low_value(event):
        current.add(event)
        continue
    boundary = file_switch(current, event) or explicit_transition(event) or semantic_drift(current, event)
    if boundary:
        chunks.append(current)
        current = new_chunk(event)
    else:
        current.add(event)
chunks.append(current)
threads = merge_revisited_topics(chunks)
```

## Revisited Topic Merge

Two chunks become one `DecisionThread` when:

- file overlap is not empty, and
- topic embedding similarity is `>= 0.75`, or
- one chunk explicitly says it is returning to the prior topic.

If chunks share a file but topic similarity is below `0.75`, they remain separate threads.

## Edge Cases

Read-only tool calls can stay inside the current chunk unless they trigger a clear file/topic switch.

A single assistant message mentioning multiple files can create a broad chunk. Later code writes split it into narrower threads if file sets diverge.

Very short chunks with no decision, code change, or durable statement can remain timeline-only and not produce a decision thread.

## Graph Effects

Creates `DecisionThread` nodes and `HAS_THREAD`, `CONTINUES_TOPIC_OF`, and event membership metadata.

## Tests

- File switch creates boundary.
- Explicit phrase creates boundary.
- Semantic drift below `0.65` creates boundary.
- Same file plus similarity `0.80` merges chunks into one thread.
- Same file plus similarity `0.30` keeps chunks separate.