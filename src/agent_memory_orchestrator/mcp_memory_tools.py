from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .daemon_client import DaemonClient, DaemonUnavailable
from .graph_service import GraphRagService
from .graph_store import GraphBackendUnavailable
from .memory_service import MemoryService
from .qwen_client import QwenUnavailable


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
        "description": "Explicit Kuzu GraphRAG retrieval over committed/session graph memory.",
        "required": ["query"],
        "returns": ["context", "nodes", "plan"],
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
}


class MemoryMcpToolService:
    """Testable implementation behind the MCP memory tools.

    The FastMCP server should only register functions and delegate here. This
    keeps contracts testable without booting an MCP transport.
    """

    def __init__(
        self,
        settings: Settings,
        memory: MemoryService | None = None,
        graph: GraphRagService | None = None,
        daemon: DaemonClient | None = None,
    ) -> None:
        self.settings = settings
        self.memory = memory or MemoryService(settings)
        self._graph = graph
        self._daemon = daemon or DaemonClient.from_settings(settings)
        self.memory.init_db()

    def close(self) -> None:
        self.memory.close()
        if self._graph is not None:
            self._graph.close()

    def health_ping(self) -> dict[str, Any]:
        return {"ok": True, "service": "agent-memory-orchestrator"}

    def config_view(self) -> dict[str, Any]:
        return {
            "ok": True,
            "local_only": self.settings.local_only,
            "mcp_transport": self.settings.mcp_transport,
            "mcp_host": self.settings.mcp_host,
            "mcp_port": self.settings.mcp_port,
            "db_path": str(self.settings.db_path),
            "export_dir": str(self.settings.export_dir),
            "embedding_dims": self.settings.embedding_dims,
            "embedding_model": self.settings.embedding_model,
            "reranker_model": self.settings.reranker_model,
            "vector_backend": self.settings.vector_backend,
            "approval_mode": self.settings.approval_mode,
            "owner_user_id": self.settings.owner_user_id,
            "workspace_id": self.settings.workspace_id,
            "project_id": self.settings.project_id,
            "visibility_scope": self.settings.visibility_scope,
            "sensitivity_level": self.settings.sensitivity_level,
            "consensus_threshold": self.settings.consensus_threshold,
            "max_review_rounds": self.settings.max_review_rounds,
            "context_budget": self.settings.context_budget,
            "reranker_backend": self.settings.reranker_backend,
            "rerank_top_k": self.settings.rerank_top_k,
            "rerank_max_chars": self.settings.rerank_max_chars,
            "graph_backend": self.settings.graph_backend,
            "graph_path": str(self.settings.graph_path),
            "evidence_dir": str(self.settings.evidence_dir),
            "qwen_runtime": self.settings.qwen_runtime,
            "qwen_model": self.settings.qwen_model,
            "qwen_endpoint": self.settings.qwen_endpoint,
        }

    def tool_contracts(self) -> dict[str, Any]:
        return {"ok": True, "tools": MCP_MEMORY_TOOL_CONTRACTS}

    def memory_write(
        self,
        *,
        session_id: str,
        agent: str,
        event_type: str,
        content: str,
        metadata_json: str = "{}",
        create_memory: bool = True,
    ) -> dict[str, Any]:
        session_id = _require_text(session_id, "session_id")
        agent = _normalize_agent(agent)
        event_type = _require_text(event_type, "event_type")
        content = _require_text(content, "content")
        metadata = _parse_metadata(metadata_json)

        if not self.memory.session_exists(session_id):
            self.memory.create_session(session_id=session_id, title=session_id)
        event = self.memory.add_event(
            session_id=session_id,
            agent=agent,
            event_type=event_type,
            content=content,
            metadata=metadata,
            source_app=agent,
            process=create_memory,
        )
        rows = self.memory.conn.execute(
            """
            SELECT id
            FROM memory_units
            WHERE source_event_id = ?
            ORDER BY created_at DESC
            """,
            (event.id,),
        ).fetchall()
        memory_ids = [row["id"] for row in rows]
        return {
            "ok": True,
            "event_id": event.id,
            "session_id": event.session_id,
            "memory_ids": memory_ids,
            "memory_id": memory_ids[0] if memory_ids else None,
            "memory_count": len(memory_ids),
            "redacted": event.redacted,
        }

    def memory_search(
        self,
        *,
        query: str,
        session_id: str = "",
        limit: int = 10,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        query = _require_text(query, "query")
        target_session = session_id or None
        safe_limit = _bounded_limit(limit, default=10, maximum=100)
        results = self.memory.search_memories(
            query=query,
            session_id=target_session,
            limit=safe_limit,
            include_historical=include_historical,
        )
        return {"ok": True, "count": len(results), "results": results}

    def memory_context_pack(
        self,
        *,
        query: str,
        session_id: str = "",
        budget: int = 2500,
        limit: int = 12,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        query = _require_text(query, "query")
        return {
            "ok": True,
            **self.memory.build_context_pack(
                query=query,
                session_id=(session_id or None),
                budget_tokens=max(1, int(budget)),
                limit=_bounded_limit(limit, default=12, maximum=100),
                include_historical=include_historical,
            ),
        }

    def memory_metrics(self) -> dict[str, Any]:
        return {"ok": True, "metrics": self.memory.inspect_metrics()}

    def memory_rebuild_indexes(self, *, force_vectors: bool = False) -> dict[str, Any]:
        return {"ok": True, "result": self.memory.rebuild_indexes(force_vectors=force_vectors)}

    def memory_timeline(self, *, session_id: str, limit: int = 50) -> dict[str, Any]:
        session_id = _require_text(session_id, "session_id")
        events = self.memory.timeline(session_id=session_id, limit=_bounded_limit(limit, default=50, maximum=500))
        return {"ok": True, "count": len(events), "events": events}

    def memory_export(self, *, out_path: str = "", session_id: str = "") -> dict[str, Any]:
        target = Path(out_path) if out_path else self.settings.export_dir / "memory_snapshot.jsonl"
        rows = self.memory.export_snapshot(out_path=target, session_id=(session_id or None))
        return {"ok": True, "rows": rows, "out_path": str(target.resolve())}

    def memory_import(self, *, in_path: str) -> dict[str, Any]:
        source = Path(_require_text(in_path, "in_path"))
        rows = self.memory.import_snapshot(source)
        indexes = self.memory.rebuild_indexes(force_vectors=False)
        return {"ok": True, "rows": rows, "in_path": str(source.resolve()), "indexes": indexes}

    def amo_graph_search(
        self,
        *,
        query: str,
        limit: int = 8,
        include_raw: bool = False,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        safe_query = _require_text(query, "query")
        safe_limit = _bounded_limit(limit, default=8, maximum=50)
        return self._graph_call(
            "amo_graph_search",
            lambda graph: graph.graph_search(
                query=safe_query,
                limit=safe_limit,
                include_raw=include_raw,
                include_historical=include_historical,
            ),
            daemon_path="/graph/search",
            daemon_method="POST",
            daemon_payload={
                "query": safe_query,
                "limit": safe_limit,
                "include_raw": include_raw,
                "include_historical": include_historical,
            },
        )

    def amo_current_context(self, *, session_id: str = "", limit: int = 8) -> dict[str, Any]:
        safe_limit = _bounded_limit(limit, default=8, maximum=50)
        return self._graph_call(
            "amo_current_context",
            lambda graph: graph.current_context(session_id=session_id, limit=safe_limit),
            daemon_path="/api/graph/session-context",
            daemon_method="GET",
            daemon_payload={"session_id": session_id, "limit": safe_limit},
        )

    def amo_decision_history(self, *, query: str, limit: int = 8) -> dict[str, Any]:
        safe_query = _require_text(query, "query")
        safe_limit = _bounded_limit(limit, default=8, maximum=50)
        return self._graph_call(
            "amo_decision_history",
            lambda graph: graph.decision_history(query=safe_query, limit=safe_limit),
            daemon_path="/graph/search",
            daemon_method="POST",
            daemon_payload={"query": safe_query, "limit": safe_limit, "include_historical": True},
        )

    def amo_work_history(self, *, query: str, limit: int = 8) -> dict[str, Any]:
        safe_query = _require_text(query, "query")
        safe_limit = _bounded_limit(limit, default=8, maximum=50)
        return self._graph_call(
            "amo_work_history",
            lambda graph: graph.work_history(query=safe_query, limit=safe_limit),
            daemon_path="/graph/search",
            daemon_method="POST",
            daemon_payload={"query": safe_query, "limit": safe_limit, "include_historical": True},
        )

    def amo_raw_evidence(self, *, query: str, limit: int = 8) -> dict[str, Any]:
        safe_query = _require_text(query, "query")
        safe_limit = _bounded_limit(limit, default=8, maximum=50)
        return self._graph_call(
            "amo_raw_evidence",
            lambda graph: graph.raw_evidence(query=safe_query, limit=safe_limit),
            daemon_path="/graph/search",
            daemon_method="POST",
            daemon_payload={
                "query": safe_query,
                "limit": safe_limit,
                "include_raw": True,
                "include_historical": True,
            },
        )

    def amo_merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        return self._graph_call(
            "amo_merge_status",
            lambda graph: graph.merge_status(session_id=session_id),
            daemon_path="/api/graph/status",
            daemon_method="GET",
            daemon_payload={"session_id": session_id},
        )

    def _graph_call(
        self,
        tool: str,
        fn,
        *,
        daemon_path: str,
        daemon_method: str,
        daemon_payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if self._graph is not None:
                return fn(self._graph)
            if daemon_method == "GET":
                return self._daemon.get(daemon_path, daemon_payload)
            return self._daemon.post(daemon_path, daemon_payload)
        except DaemonUnavailable as exc:
            return {"ok": False, "tool": tool, "requires_daemon": True, "error": str(exc)}
        except (GraphBackendUnavailable, QwenUnavailable) as exc:
            return {"ok": False, "tool": tool, "error": str(exc)}


def _require_text(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _normalize_agent(agent: str) -> str:
    normalized = _require_text(agent, "agent").lower()
    if normalized not in AGENTS:
        raise ValueError(f"agent must be one of: {', '.join(sorted(AGENTS))}")
    return normalized


def _parse_metadata(metadata_json: str) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata_json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("metadata_json must be a JSON object")
    return parsed


def _bounded_limit(value: int, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))
