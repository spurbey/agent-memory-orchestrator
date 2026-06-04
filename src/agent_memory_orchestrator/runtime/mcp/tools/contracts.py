from __future__ import annotations

from typing import Any

AGENTS = {"claude", "codex", "user", "system"}


MCP_MEMORY_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "memory_write": {
        "description": "Persist a local memory event and optionally extract durable memory units.",
        "required": ["session_id", "agent", "event_type", "content"],
        "returns": ["event_id", "memory_ids", "memory_count"],
    },
    "memory_search": {
        "description": "Hybrid local retrieval over BM25/vector/KG with provenance and score traces.",
        "required": ["query"],
        "returns": ["count", "results"],
    },
    "memory_context_pack": {
        "description": "Build a bounded agent-ready memory context packet with exclusions and provenance.",
        "required": ["query"],
        "returns": ["text", "items", "excluded", "retrieval_run_id"],
    },
    "memory_timeline": {
        "description": "Read raw redacted session events for audit/debugging.",
        "required": ["session_id"],
        "returns": ["count", "events"],
    },
    "memory_export": {
        "description": "Export canonical local memory rows to JSONL.",
        "required": [],
        "returns": ["rows", "out_path"],
    },
    "memory_import": {
        "description": "Import a JSONL memory snapshot.",
        "required": ["in_path"],
        "returns": ["rows"],
    },
    "amo_graph_search": {
        "description": "Explicit graph memory search. With repo_id, uses active production repository memory; without repo_id, uses global graph search for debug and raw-evidence inspection.",
        "required": ["query"],
        "returns": ["context", "nodes", "plan", "context_for_synthesis", "hits", "version_history"],
    },
    "amo_current_context": {
        "description": "Read current graph context without automatic hook retrieval.",
        "required": [],
        "returns": ["nodes"],
    },
    "amo_decision_history": {
        "description": "Retrieve active and historical decision graph nodes.",
        "required": ["query"],
        "returns": ["context", "nodes", "plan"],
    },
    "amo_work_history": {
        "description": "Retrieve work-change and commit-linked graph history.",
        "required": ["query"],
        "returns": ["context", "nodes", "plan"],
    },
    "amo_raw_evidence": {
        "description": "Retrieve raw evidence refs only when explicitly requested.",
        "required": ["query"],
        "returns": ["context", "nodes", "plan"],
    },
    "amo_merge_status": {
        "description": "Inspect Kuzu graph merge status for a session or central graph.",
        "required": [],
        "returns": ["counts"],
    },
    "peer_memory_ask": {
        "description": "Ask local AMO memory first, then query trusted peer agents when local confidence is low.",
        "required": ["query"],
        "returns": ["mode", "answer", "room_id", "local_quality", "peer_responses", "citations", "timing"],
    },
    "peer_room_status": {
        "description": "Inspect peer-agent room lifecycle and idempotency state.",
        "required": ["room_id"],
        "returns": ["room", "agent_state"],
    },
    "peer_room_ask": {
        "description": "Send a schema-valid follow-up request inside an existing peer-agent room, optionally waiting for targeted responses.",
        "required": ["room_id", "query"],
        "returns": ["room_id", "mode", "logical_request_id", "peer_requests", "deliveries", "peer_responses", "timing"],
    },
    "peer_room_continue": {
        "description": "Let the initiator planner choose one next action for a peer-agent room.",
        "required": ["room_id"],
        "returns": ["room_id", "action", "plan", "followup", "finalize"],
    },
    "peer_room_context": {
        "description": "Read the local three-layer context pack for a peer-agent room.",
        "required": ["room_id"],
        "returns": ["context"],
    },
    "peer_room_messages": {
        "description": "Read local peer-agent room transcript messages.",
        "required": ["room_id"],
        "returns": ["messages"],
    },
}


__all__ = ["AGENTS", "MCP_MEMORY_TOOL_CONTRACTS"]
