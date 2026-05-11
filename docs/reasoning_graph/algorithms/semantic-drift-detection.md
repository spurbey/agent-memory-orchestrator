# Semantic Drift Detection

## Depends on
- ../modules/embeddings-runtime.md
- chunking-and-decision-threads.md

## Used by
- chunking-and-decision-threads.md
- same-file-resolution.md

## Related docs
- ../modules/qwen-contracts.md
- ../graph_model/node-types.md

## Inputs

Ordered assistant-message texts inside a candidate session chunk. Only visible assistant messages are used. Tool output and raw JSON are excluded from drift windows.

## Output

Boolean boundary decision and similarity score.

## Algorithm

Use rolling windows of the last 3 assistant messages. Embed each window text with BGE-M3. Compare consecutive windows with cosine similarity.

```python
window_a = "\n".join(messages[i-3:i])
window_b = "\n".join(messages[i-2:i+1])
vec_a = bge_m3.embed(window_a)
vec_b = bge_m3.embed(window_b)
similarity = cosine(vec_a, vec_b)
if similarity < 0.65:
    boundary = True
else:
    boundary = False
```

## Threshold

`0.65` is the default boundary threshold. Exactly `0.65` means same topic. Below `0.65` means semantic drift.

## Fallback

If embeddings are unavailable, semantic drift cannot create a high-confidence boundary. The chunker uses deterministic file switch and explicit phrase rules only and records `embedding_status=missing`.

## Example

Messages about NDK, Gradle, and build errors produce high similarity. A sudden switch to dashboard CSS should drop similarity below threshold and create a chunk boundary.

## Graph Effects

No graph node is created directly by this algorithm. It annotates chunk diagnostics used to create `DecisionThread` nodes.

## Tests

- Similar windows produce similarity above threshold.
- Different topic windows produce boundary.
- Missing embeddings records fallback diagnostic and does not claim semantic boundary.