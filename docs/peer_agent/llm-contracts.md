# Peer-Agent LLM Contracts

This is the peer-agent LLM contract for AMO bot-to-bot rooms. Implementation must not improvise prompt roles, schemas, mutation behavior, or provider ownership outside this document without updating the contract and tests.

## Product Boundary

AMO peer-agent communication is local-first. The Go `amo-peer-netd` sidecar only transports signed/authenticated envelopes. Python AMO owns memory retrieval, room state, policy, prompt construction, summarization, and final synthesis.

Provider credentials are never sent to peers. Responder-side drafting may use that responder device's local Ollama only. Initiator-side planning and final synthesis may use the initiator device's local Ollama first, then the initiator's configured OpenAI-compatible API fallback.

## Context Window Contract

Every peer-agent LLM prompt is built from bounded structured inputs. The room context has three layers:

1. `room_md`: initiator-owned room brief with topic, initiator, participants, share boundary, and desired output.
2. `rolling_summary_md`: initiator-owned summary of earlier room discussion once transcript size crosses the summary limit.
3. Recent room exchanges:
   - `group_recent_messages`: group-visible messages only.
   - `pairwise_recent_messages`: for initiator, grouped request/response orchestration; for a peer, only tagged initiator-peer exchanges involving that peer.
   - `recent_messages`: the active scoped view for the current role.

`open_questions` must contain only unanswered request IDs. A `context_request` is answered when a `context_response` references the same `request_id`.

## Contract 1: Peer Responder Answer

Owner: responding peer device.

Provider policy: local Ollama only. No hosted provider fallback is allowed on the peer for another user's request.

Input:

```json
{
  "query": "string",
  "retrieval_bundle": {
    "answer": {"text": "bounded retrieval text"},
    "support": []
  },
  "quality": {
    "answer_grade": false,
    "confidence": 0.0,
    "reasons": [],
    "gaps": []
  },
  "room_context": {
    "room_id": "room_...",
    "role": "peer",
    "layers": {}
  }
}
```

Output schema:

```json
{
  "answer": "string",
  "confidence": 0.0,
  "answer_grade": false,
  "gaps": []
}
```

Allowed mutation: may send exactly one `context_response` for the tagged `request_id`. It may not mutate local memory, remote memory, room summary, final synthesis, peer config, or raw evidence.

Fallback: if local Ollama is unavailable or generation fails, AMO sends `retrieval_bundle` when `peer_agent_allow_retrieval_only_responses=true`; otherwise it sends `low_confidence`.

## Contract 2: Initiator Room Continuation Planner

Owner: initiator device.

Provider policy: local Ollama first. If unavailable and `peer_agent_allow_initiator_api_fallback=true`, use the initiator's OpenAI-compatible provider config. API keys never leave the initiator device.

Input:

```json
{
  "room_context": {
    "room_id": "room_...",
    "role": "initiator",
    "layers": {}
  },
  "peer_responses": [],
  "agent_state": {
    "status": "open",
    "original_query": "string",
    "peer_requests": [],
    "planner_actions": []
  }
}
```

Output schema:

```json
{
  "action": "finalize|ask_peer|ask_peers|wait",
  "peer_ids": [],
  "query": "string",
  "reason": "string",
  "confidence": 0.0
}
```

Allowed mutation through AMO only:

- `finalize`: write local-only `final_synthesis` on the initiator.
- `ask_peer`: send one schema-valid `context_request` to one tagged peer.
- `ask_peers`: send one logical request fan-out to multiple tagged peers.
- `wait`: no room mutation except planner action audit.

The planner must not request raw evidence by default. It should ask short follow-up questions only when peer responses leave a specific gap.

## Contract 3: Initiator Final Synthesis

Owner: initiator device.

Provider policy: local Ollama first, then initiator-owned provider fallback if configured.

Input:

```json
{
  "query": "string",
  "local_result": {},
  "peer_responses": []
}
```

Output schema:

```json
{
  "answer": "string",
  "confidence": 0.0,
  "mode": "local_only|peer_assisted|retrieval_only",
  "gaps": []
}
```

Allowed mutation: writes local-only `final_synthesis` by default. It must not broadcast final synthesis unless a later explicit sanitized-broadcast policy is implemented.

## Contract 4: Room Summary

Owner: initiator device.

Provider policy: local Ollama only in v1. If unavailable, AMO uses deterministic summary fallback.

Input:

```json
{
  "room_context": {
    "layers": {
      "room_md": "string",
      "rolling_summary_md": "string",
      "recent_messages": []
    }
  }
}
```

Output schema:

```json
{
  "summary_md": "markdown string"
}
```

Allowed mutation: replace initiator-owned `rolling_summary.md` and update summary state. It must preserve unresolved questions and recent decisions.

## Message Contracts

`context_request` metadata must include:

```json
{
  "schema_version": 1,
  "agent_room_schema_version": 1,
  "logical_request_id": "q_...",
  "request_id": "req_...",
  "room_id": "room_...",
  "audience": "peer",
  "target_peer_id": "peer-node-id",
  "query": "string",
  "deadline_at": "iso8601",
  "requested_capabilities": ["graph_retrieval", "memory_search", "llm_answer"],
  "raw_evidence_requested": false
}
```

`context_response` metadata must include:

```json
{
  "schema_version": 1,
  "request_id": "req_...",
  "parent_message_id": "msg_...",
  "audience": "initiator",
  "target_peer_id": "initiator-node-id",
  "mode": "llm_answer|retrieval_bundle|needs_approval|low_confidence",
  "answer_grade": false,
  "quality": {},
  "support": [],
  "retrieval_bundle": {},
  "timing": {
    "retrieval_ms": 0,
    "quality_support_ms": 0,
    "llm_ready": false,
    "llm_ready_ms": 0,
    "llm_generate_ms": 0,
    "total_ms": 0
  }
}
```

## Latency Policy

Normal unattended participation should prefer reliable fast responses over forced model generation. A peer may return `retrieval_bundle` when local Ollama is not already loaded. Do not auto-pull or auto-load large models inside a peer response path.

Every peer response should expose timing metadata sufficient to classify latency as:

- retrieval/index latency: `retrieval_ms`
- support/quality shaping latency: `quality_support_ms`
- local model readiness probe latency: `llm_ready_ms`
- local model generation latency: `llm_generate_ms`
- local response assembly latency: `total_ms`

Network delivery latency is measured by the initiator around send/wait and by sidecar delivery diagnostics.

## Validation Rules

AMO must reject or skip peer-agent requests when:

- `schema_version` is missing or unsupported.
- `agent_room_schema_version` is missing for automated peer-agent messages.
- `target_peer_id` does not match the local node.
- transport identity is not verified by remote libp2p peer ID or signed envelope policy.
- deadline has expired.
- raw evidence is requested but policy requires approval.

AMO must not mark a request as sent/responded unless delivery succeeds. Duplicate envelopes must not create duplicate responses for the same `request_id`.
