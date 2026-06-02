from __future__ import annotations

from typing import Any

from ...daemon.client import DaemonUnavailable
from ....infrastructure.kuzu import GraphBackendUnavailable
from ....infrastructure.llm import QwenUnavailable
from .graph_results import (
    _indexed_graph_retrieval_ready,
    _indexed_unavailable_context,
    _mcp_graph_result_from_indexed,
)
from .repository_resolution import resolve_active_repo_id
from .validation import bounded_limit as _bounded_limit
from .validation import require_text as _require_text


class GraphToolMixin:
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


__all__ = ["GraphToolMixin"]
