from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...daemon.client import DaemonClient, DaemonUnavailable
from ....core.config import Settings
from ....graph.service import GraphRagService
from ....graph.store import GraphBackendUnavailable
from ....llm.qwen import QwenUnavailable
from ....memory import MemoryService
from ....peer.agent import PeerAgentService


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
        "description": "Explicit graph memory search. With repo_id, uses active V2 repository memory; without repo_id, uses the legacy graph search path.",
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
        safe_repo_id = str(repo_id or "").strip()
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
            indexed.setdefault("retrieval_source", "v2_active_projection")
            indexed.setdefault("context_for_synthesis", _indexed_unavailable_context(indexed))
            indexed.setdefault("hits", [])
            indexed.setdefault("version_history", [])
            indexed.setdefault(
                "plan",
                {
                    "intent": "semantic_search",
                    "source": "v2_active_projection",
                    "repo_id": safe_repo_id,
                },
            )
            return indexed
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


def _indexed_graph_retrieval_ready(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is not True:
        return False
    retrieval = payload.get("retrieval") if isinstance(payload.get("retrieval"), dict) else {}
    hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
    answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
    return bool(hits or str(answer.get("text") or "").strip() or payload.get("central_answer_trace"))


def _indexed_unavailable_context(payload: dict[str, Any]) -> str:
    reason = str(payload.get("error") or "active_projection_missing")
    return (
        "AMO V2 central retrieval is unavailable for this repository. "
        f"Reason: {reason}. Build/apply the active retrieval projection before using repository memory."
    )


def _mcp_graph_result_from_indexed(
    payload: dict[str, Any],
    *,
    tool: str,
    query: str,
    repo_id: str,
    limit: int,
) -> dict[str, Any]:
    retrieval = payload.get("retrieval") if isinstance(payload.get("retrieval"), dict) else {}
    hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
    answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
    public_hits = [
        _mcp_agent_hit_from_retrieval_hit(idx, hit)
        for idx, hit in enumerate(hits[:limit], 1)
        if isinstance(hit, dict)
    ]
    version_history = _version_history_from_answer_and_hits(answer=answer, public_hits=public_hits)
    context = str(answer.get("text") or "").strip()
    if not context:
        context = "Use these retrieved memory hits to answer the user. Do not treat this as final prose."
    return {
        "ok": True,
        "tool": tool,
        "query": query,
        "retrieval_mode": "v2_active_repository_memory",
        "repo": {"id": repo_id},
        "context_for_synthesis": context,
        "hits": public_hits,
        "version_history": version_history,
        "retrieval_status": {
            "vector": str(retrieval.get("vector_status") or ""),
            "source": "v2_active_projection",
            "repo_id": repo_id,
        },
        "answer_trace": payload.get("central_answer_trace") or {},
    }


def _mcp_agent_hit_from_retrieval_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document") if isinstance(hit.get("document"), dict) else {}
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    doc_type = str(document.get("doc_type") or "")
    atom_kind = str(node_metadata.get("atom_kind") or "")
    kind = atom_kind or doc_type or str(document.get("node_kind") or "")
    files = _public_files(document=document, metadata=metadata, version_metadata=version_metadata)
    commit = _public_commit(document=document, metadata=metadata, version_metadata=version_metadata)
    return {
        "rank": rank,
        "kind": kind,
        "doc_type": doc_type,
        "title": str(document.get("title") or ""),
        "summary": _public_hit_summary(document=document, metadata=metadata, version_metadata=version_metadata),
        "why_it_matched": _why_hit_matched(hit=hit, document=document, files=files),
        "status": _public_status(document=document, node_metadata=node_metadata, version_metadata=version_metadata),
        "commit": commit,
        "files": files,
        "evidence": _public_evidence(metadata=metadata),
        "score": hit.get("score"),
    }


def _public_hit_summary(*, document: dict[str, Any], metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    doc_type = str(document.get("doc_type") or "")
    body = str(document.get("body") or "")
    if doc_type == "central_version":
        atom_kind = str((metadata.get("node_metadata") or {}).get("atom_kind") or "")
        if atom_kind == "file":
            file_path = _first_text(version_metadata.get("file_path"), _body_field(body, "file_path"))
            producing_commit_sha = _first_text(version_metadata.get("producing_commit_sha"), _body_field(body, "producing_commit_sha"))
            suffix = f" produced by commit {producing_commit_sha[:12]}" if producing_commit_sha else ""
            return f"Active file memory for {file_path}{suffix}." if file_path else "Active file memory."
        for key in ("statement", "summary", "rationale"):
            text = str(version_metadata.get(key) or "").strip()
            if text:
                return _compact_text(text, 900)
    evidence_summary = _summary_from_public_evidence(_public_evidence(metadata=metadata))
    if evidence_summary:
        return evidence_summary
    reasons = metadata.get("reasons")
    if isinstance(reasons, list) and reasons:
        return _compact_text(" ".join(str(reason) for reason in reasons if str(reason).strip()), 900)
    for prefix in ("FileImpactSummary:", "CodeImpactSummary:", "Packet:"):
        body = body.replace(prefix, "").strip()
    return _compact_text(body.split("\n{", 1)[0], 900)


def _public_status(*, document: dict[str, Any], node_metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    for value in (node_metadata.get("status"), version_metadata.get("status"), document.get("memory_class")):
        text = str(value or "").strip()
        if text:
            return text
    return "retrieved"


def _public_commit(
    *,
    document: dict[str, Any],
    metadata: dict[str, Any],
    version_metadata: dict[str, Any],
) -> dict[str, str]:
    commit_sha = _first_text(
        document.get("commit_sha"),
        metadata.get("commit_sha"),
        version_metadata.get("producing_commit_sha"),
        _first_list_value(version_metadata.get("linked_commits")),
        _body_field(str(document.get("body") or ""), "producing_commit_sha"),
    )
    commit = metadata.get("commit") if isinstance(metadata.get("commit"), dict) else {}
    message = _first_text(commit.get("message"), _first_list_value(metadata.get("commit_messages")))
    if not commit_sha and not message:
        return {}
    return {"sha": commit_sha[:12], "message": message}


def _public_files(
    *,
    document: dict[str, Any],
    metadata: dict[str, Any],
    version_metadata: dict[str, Any],
) -> list[str]:
    values: list[Any] = [
        version_metadata.get("linked_files"),
        metadata.get("linked_files"),
        metadata.get("selected_files"),
        metadata.get("changed_file_sample"),
        metadata.get("path"),
        metadata.get("file_path"),
        version_metadata.get("file_path"),
        _body_field(str(document.get("body") or ""), "file_path"),
    ]
    return _unique_public_values(values, limit=8)


def _public_evidence(*, metadata: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for role, key in (("user_goal", "problem_refs"), ("rationale", "rationale_refs"), ("validation", "validation_refs")):
        refs = metadata.get(key)
        if not isinstance(refs, list):
            continue
        for ref in refs[:3]:
            if not isinstance(ref, dict):
                continue
            summary = str(ref.get("excerpt") or ref.get("output_preview") or ref.get("summary") or "").strip()
            if summary:
                evidence.append({"role": role, "summary": _compact_text(summary, 500)})
    return evidence[:6]


def _summary_from_public_evidence(evidence: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for role, label in (("user_goal", "User goal"), ("rationale", "Rationale"), ("validation", "Validation")):
        summary = next((item["summary"] for item in evidence if item.get("role") == role and item.get("summary")), "")
        if summary:
            parts.append(f"{label}: {summary}")
    return _compact_text(" ".join(parts), 900)


def _why_hit_matched(*, hit: dict[str, Any], document: dict[str, Any], files: list[str]) -> str:
    doc_type = str(document.get("doc_type") or "")
    if doc_type == "central_version":
        return "Matched active central memory" + (f" for {', '.join(files[:2])}" if files else "") + "."
    if doc_type in {"file_impact", "code_impact"}:
        return "Matched curated code/file impact support" + (f" for {', '.join(files[:2])}" if files else "") + "."
    if doc_type == "packet":
        return "Matched the original work packet and captured user/agent discussion."
    reasons = hit.get("reasons") if isinstance(hit.get("reasons"), list) else []
    term_reason = next((str(reason).removeprefix("term_overlap:") for reason in reasons if str(reason).startswith("term_overlap:")), "")
    if term_reason:
        return f"Matched query terms: {term_reason.replace(',', ', ')}."
    return "Matched active repository memory for the query."


def _version_history_from_answer_and_hits(*, answer: dict[str, Any], public_hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    context = answer.get("context") if isinstance(answer.get("context"), dict) else {}
    timeline = context.get("version_timeline") if isinstance(context.get("version_timeline"), dict) else {}
    entries = timeline.get("entries") if isinstance(timeline.get("entries"), list) else []
    history: list[dict[str, str]] = []
    for entry in entries[:8]:
        if not isinstance(entry, dict):
            continue
        history.append(
            {
                "commit": str(entry.get("commit_sha") or "")[:12],
                "message": str(entry.get("message") or ""),
                "summary": _compact_text(str(entry.get("why") or ""), 500),
            }
        )
    if history:
        return history
    for hit in public_hits:
        commit = hit.get("commit") if isinstance(hit.get("commit"), dict) else {}
        sha = str(commit.get("sha") or "")
        if sha:
            history.append(
                {
                    "commit": sha,
                    "message": str(commit.get("message") or ""),
                    "summary": _compact_text(str(hit.get("summary") or ""), 500),
                }
            )
    return history[:8]


def _body_field(body: str, key: str) -> str:
    prefix = f"{key.strip().lower()}:"
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[-1].strip()
    return ""


def _first_list_value(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or "").strip()
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _compact_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 14)].rstrip() + " ... <clipped>"


def _unique_public_values(values: list[Any], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)
            return
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for value in values:
        visit(value)
        if len(out) >= limit:
            break
    return out[:limit]


