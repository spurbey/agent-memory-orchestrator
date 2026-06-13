from __future__ import annotations

from typing import Any

from ...daemon.client import DaemonClient
from ....application.services.memory_graph.service import GraphRagService
from ....core.config import Settings
from ....memory import MemoryService
from ....peer.agent import PeerAgentService
from .contracts import MCP_MEMORY_TOOL_CONTRACTS
from .graph_service import GraphToolMixin
from .memory_service import MemoryToolMixin
from .peer_service import PeerToolMixin
from .semantic_harness_service import SemanticHarnessToolMixin


class MemoryMcpToolService(MemoryToolMixin, GraphToolMixin, PeerToolMixin, SemanticHarnessToolMixin):
    """Testable implementation behind the MCP memory tools.

    FastMCP registers transport functions in ``runtime.mcp.server`` and
    delegates here. Capability-specific tool behavior lives in mixins under
    this package so memory, graph, and peer-agent surfaces can evolve without
    turning the MCP package root into a mixed implementation file.
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
        self._close_semantic_harness_runtime()
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


__all__ = ["MemoryMcpToolService"]
