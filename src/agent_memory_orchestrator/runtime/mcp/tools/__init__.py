from __future__ import annotations

from pathlib import Path
from typing import Any

from ...daemon.client import DaemonClient, DaemonUnavailable
from ....core.config import Settings
from ....application.services.memory_graph.service import GraphRagService
from ....infrastructure.kuzu import GraphBackendUnavailable
from ....infrastructure.llm import QwenUnavailable
from ....memory import MemoryService
from ....peer.agent import PeerAgentService
from .contracts import MCP_MEMORY_TOOL_CONTRACTS
from .graph_results import (
    _indexed_graph_retrieval_ready,
    _indexed_unavailable_context,
    _mcp_graph_result_from_indexed,
)
from .repository_resolution import resolve_active_repo_id
from .validation import bounded_limit as _bounded_limit
from .validation import normalize_agent as _normalize_agent
from .validation import parse_metadata as _parse_metadata
from .validation import require_text as _require_text


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
        peer_agent: PeerAgentService | None = None,
    ) -> None:
        self.settings = settings
        self.memory = memory or MemoryService(settings)
        self._graph = graph
        self._daemon = daemon or DaemonClient.from_settings(settings, timeout_seconds=60)
        self._peer_agent = peer_agent
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
            "qwen_timeout_seconds": self.settings.qwen_timeout_seconds,
            "qwen_planner_timeout_seconds": self.settings.qwen_planner_timeout_seconds,
            "qwen_extract_timeout_seconds": self.settings.qwen_extract_timeout_seconds,
            "qwen_compress_timeout_seconds": self.settings.qwen_compress_timeout_seconds,
            "qwen_num_ctx": self.settings.qwen_num_ctx,
            "drain_max_windows_per_run": self.settings.drain_max_windows_per_run,
            "peer_agent_enabled": self.settings.peer_agent_enabled,
            "peer_agent_runtime": self.settings.peer_agent_runtime,
            "peer_agent_model": self.settings.peer_agent_model,
            "peer_agent_endpoint": self.settings.peer_agent_endpoint,
            "peer_agent_api_provider": self.settings.peer_agent_api_provider,
            "peer_agent_api_base_url": self.settings.peer_agent_api_base_url,
            "peer_agent_api_model": self.settings.peer_agent_api_model,
            "peer_agent_api_key_env": self.settings.peer_agent_api_key_env,
            "peer_agent_allow_initiator_api_fallback": self.settings.peer_agent_allow_initiator_api_fallback,
            "peer_agent_allow_retrieval_only_responses": self.settings.peer_agent_allow_retrieval_only_responses,
            "peer_agent_min_confidence": self.settings.peer_agent_min_confidence,
            "peer_agent_strong_confidence": self.settings.peer_agent_strong_confidence,
            "peer_agent_max_peers": self.settings.peer_agent_max_peers,
            "peer_agent_room_timeout_seconds": self.settings.peer_agent_room_timeout_seconds,
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
        repo_id: str = "",
        use_vector: bool = True,
        require_vector: bool = False,
    ) -> dict[str, Any]:
        safe_query = _require_text(query, "query")
        safe_limit = _bounded_limit(limit, default=8, maximum=50)
        requested_repo_id = str(repo_id or "").strip()
        safe_repo_id = resolve_active_repo_id(self.settings, requested_repo_id)
        if safe_repo_id:
            indexed = self._indexed_graph_call(
                tool="amo_graph_search",
                query=safe_query,
                limit=safe_limit,
                repo_id=safe_repo_id,
                use_vector=use_vector,
                require_vector=require_vector,
            )
            if _indexed_graph_retrieval_ready(indexed):
                return _mcp_graph_result_from_indexed(
                    indexed,
                    tool="amo_graph_search",
                    query=safe_query,
                    repo_id=safe_repo_id,
                    limit=safe_limit,
                )
            indexed.setdefault("tool", "amo_graph_search")
            indexed.setdefault("retrieval_source", "active_projection")
            indexed.setdefault("context_for_synthesis", _indexed_unavailable_context(indexed))
            indexed.setdefault("hits", [])
            indexed.setdefault("version_history", [])
            indexed.setdefault(
                "plan",
                {
                    "intent": "semantic_search",
                    "source": "active_projection",
                    "repo_id": safe_repo_id,
                },
            )
            return indexed
        del include_raw, include_historical, requested_repo_id
        return {
            "ok": False,
            "tool": "amo_graph_search",
            "error": "active_repo_projection_missing",
            "retrieval_source": "active_projection",
            "context_for_synthesis": (
                "No active repository memory projection is available. "
                "Run the V2 production pipeline and retrieval projection before using AMO MCP graph search."
            ),
            "hits": [],
            "version_history": [],
        }

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

    def peer_memory_ask(
        self,
        *,
        query: str,
        session_id: str = "",
        min_confidence: float = 0.72,
        timeout_seconds: float = 45,
    ) -> dict[str, Any]:
        return self._peer_agent_service().ask(
            query=_require_text(query, "query"),
            session_id=session_id,
            min_confidence=min_confidence,
            timeout_seconds=timeout_seconds,
        )

    def peer_room_status(self, *, room_id: str) -> dict[str, Any]:
        return self._peer_agent_service().status(_require_text(room_id, "room_id"))

    def peer_room_context(self, *, room_id: str) -> dict[str, Any]:
        return self._peer_agent_service().context(_require_text(room_id, "room_id"))

    def peer_room_messages(self, *, room_id: str) -> dict[str, Any]:
        return self._peer_agent_service().messages(_require_text(room_id, "room_id"))

    def _peer_agent_service(self) -> PeerAgentService:
        if self._peer_agent is None:
            self._peer_agent = PeerAgentService(self.settings)
        return self._peer_agent

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

    def _indexed_graph_call(
        self,
        *,
        tool: str,
        query: str,
        limit: int,
        repo_id: str,
        use_vector: bool,
        require_vector: bool,
    ) -> dict[str, Any]:
        try:
            if self._graph is not None:
                return self._graph.retrieve_indexed_graph(
                    query=query,
                    repo_id=repo_id,
                    limit=limit,
                    use_vector=use_vector,
                    require_vector=require_vector,
                    include_answer=True,
                )
            return self._daemon.post(
                "/graph/retrieve",
                {
                    "query": query,
                    "repo_id": repo_id,
                    "limit": limit,
                    "use_vector": use_vector,
                    "require_vector": require_vector,
                    "include_answer": True,
                },
            )
        except DaemonUnavailable as exc:
            return {"ok": False, "tool": tool, "requires_daemon": True, "error": str(exc)}
        except (GraphBackendUnavailable, QwenUnavailable, ValueError) as exc:
            return {"ok": False, "tool": tool, "error": str(exc)}
