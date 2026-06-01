from __future__ import annotations

from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....domain.retrieval.policy import _sanitize_output_node
from ....evidence.drain import EvidenceDrain
from ....evidence.raw_store import RawEvidenceStore
from ....infrastructure.kuzu import GraphStore, KuzuGraphStore
from ....infrastructure.llm import OllamaQwenClient, QwenPlanner
from ....versioning import LocalGitBackend, VersionBackend, WorkLedger
from ..retrieval.answer_trace import _active_central_versions_for_support as _active_central_versions_for_support
from ..retrieval.runtime import embed_retrieval_index as _embed_retrieval_index
from ..retrieval.runtime import rebuild_retrieval_index as _rebuild_retrieval_index
from ..retrieval.runtime import retrieve_indexed_graph as _retrieve_indexed_graph
from ..session.detail import _evidence_roots
from ..session.detail import build_session_detail_fallback as build_session_detail_fallback
from .browser import jobs_by_session_map as _jobs_by_session_map
from .browser import list_repositories as _list_repositories
from .browser import session_detail as _session_detail
from .browser import session_overview as _session_overview
from .central_graph import central_graph as _central_graph
from .capture import capture_hook as _capture_hook
from .repo_scope import matches_repo_scope
from .search import graph_search as _graph_search
from .version_flow import _build_central_version_flows
from .version_flow import _build_version_flow
from .version_flow import _matches_commit
from .version_flow import _matches_version_flow_filter
from .version_flow import _version_flow_warnings


class GraphRagService:
    """Kuzu-backed GraphRAG service.

    Hooks use this only for lightweight evidence capture. MCP/daemon use it for
    full graph retrieval and Qwen-backed context generation.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        store: GraphStore | None = None,
        planner: QwenPlanner | None = None,
        version_backend: VersionBackend | None = None,
        evidence_store: RawEvidenceStore | None = None,
        read_only: bool = False,
    ) -> None:
        self.settings = settings
        self.store = store or KuzuGraphStore(settings.graph_path, read_only=read_only)
        self.read_only = bool(read_only or getattr(self.store, "read_only", False))
        self.planner = planner or OllamaQwenClient(
            endpoint=settings.qwen_endpoint,
            model=settings.qwen_model,
            timeout_seconds=settings.qwen_timeout_seconds,
            planner_timeout_seconds=min(settings.qwen_timeout_seconds, settings.qwen_planner_timeout_seconds),
            compression_timeout_seconds=min(settings.qwen_timeout_seconds, settings.qwen_compress_timeout_seconds),
            num_ctx=settings.qwen_num_ctx,
        )
        self.version_backend = version_backend or LocalGitBackend()
        self.evidence = evidence_store or RawEvidenceStore(settings.evidence_dir)
        if not self.read_only:
            self.store.init_schema()

    def close(self) -> None:
        self.store.close()

    def capture_hook(self, payload: dict[str, Any], *, default_agent: str = "codex") -> dict[str, Any]:
        return _capture_hook(
            settings=self.settings,
            graph_store=self.store,
            evidence_store=self.evidence,
            version_backend=self.version_backend,
            startup_context=self.startup_context,
            payload=payload,
            default_agent=default_agent,
        )

    def startup_context(self, *, session_id: str, source_app: str, git: dict[str, Any] | None = None) -> str:
        repo = ""
        if git and git.get("available"):
            repo = f" repo={git.get('repo_root')} branch={git.get('branch')} head={str(git.get('head') or '')[:12]}"
        return (
            "AMO GraphRAG is active for this session. "
            "Hooks are capture-only; use MCP tool amo_graph_search for explicit memory retrieval. "
            f"session={session_id} app={source_app}{repo}"
        )

    def graph_search(
        self,
        *,
        query: str,
        limit: int = 8,
        include_raw: bool = False,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        return _graph_search(
            store=self.store,
            planner=self.planner,
            query=query,
            limit=limit,
            include_raw=include_raw,
            include_historical=include_historical,
        )

    def current_context(self, *, session_id: str = "", limit: int = 8) -> dict[str, Any]:
        safe_limit = max(1, min(25, int(limit)))
        del safe_limit
        return {
            "ok": True,
            "session_id": session_id,
            "count": 0,
            "nodes": [],
            "context": "Current context is captured as raw evidence and production session jobs; query active repository memory for answer-grade context.",
        }

    def decision_history(self, *, query: str, limit: int = 8) -> dict[str, Any]:
        return self.graph_search(query=query, limit=limit, include_historical=True)

    def work_history(self, *, query: str, limit: int = 8) -> dict[str, Any]:
        return self.graph_search(query=query, limit=limit, include_historical=True)

    def raw_evidence(self, *, query: str, limit: int = 8) -> dict[str, Any]:
        return self.graph_search(query=query, limit=limit, include_raw=True, include_historical=True)

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        return {"ok": True, **self.store.merge_status(session_id=session_id)}

    def drain_evidence(self, *, limit: int = 500, session_id: str = "", max_windows: int | None = None) -> dict[str, Any]:
        drain = self._new_drain()
        return drain.drain(limit=max(1, int(limit)), session_id=session_id, max_windows=max_windows)

    def pending_evidence(self, *, session_id: str = "") -> dict[str, Any]:
        drain = self._new_drain()
        return drain.pending(session_id=session_id)

    def session_overview(self, *, limit: int = 25, repo_id: str = "") -> dict[str, Any]:
        return _session_overview(
            settings=self.settings,
            graph_store=self.store,
            merge_status=self.merge_status,
            limit=limit,
            repo_id=repo_id,
        )

    def list_repositories(self, *, limit: int = 200) -> dict[str, Any]:
        return _list_repositories(settings=self.settings, graph_store=self.store, limit=limit)

    def _jobs_by_session(self, *, limit: int = 5000) -> dict[str, dict[str, Any]]:
        return _jobs_by_session_map(self.settings, limit=limit)

    def session_detail(self, *, session_id: str, limit: int = 120) -> dict[str, Any]:
        return _session_detail(
            settings=self.settings,
            graph_store=self.store,
            current_context=self.current_context,
            merge_status=self.merge_status,
            central_graph=self.central_graph,
            session_id=session_id,
            limit=limit,
        )

    def central_graph(self, *, limit: int = 100, full: bool = False, repo_id: str = "") -> dict[str, Any]:
        return _central_graph(
            graph_store=self.store,
            merge_status=self.merge_status,
            limit=limit,
            full=full,
            repo_id=repo_id,
        )

    def version_flow(self, *, commit: str = "", session_id: str = "", repo_id: str = "", limit: int = 100) -> dict[str, Any]:
        """Return commit-centric provenance and versioning flow for the web UI.

        This is intentionally graph-derived. It does not infer from raw logs;
        it shows what AMO has actually promoted into central graph memory.
        """

        safe_limit = max(1, min(500, int(limit)))
        safe_repo_id = str(repo_id or "").strip()
        node_limit = max(1000, safe_limit * 16)
        edge_limit = max(2000, safe_limit * 32)
        nodes = [
            _sanitize_output_node(node)
            for node in self.store.list_nodes(limit=node_limit)
            if matches_repo_scope(node, safe_repo_id)
        ]
        central_nodes = [
            _sanitize_output_node(node)
            for node in self.store.list_nodes(
                kinds=["GraphCommit", "KnowledgeAtom", "KnowledgeVersion"],
                limit=max(node_limit, safe_limit * 300),
            )
            if matches_repo_scope(node, safe_repo_id)
        ]
        nodes_by_id = {str(node.get("id") or ""): node for node in [*nodes, *central_nodes]}
        nodes = list(nodes_by_id.values())
        edges = self.store.list_edges(limit=edge_limit)
        central_edges = self.store.list_edges(kinds=["VERSION_OF"], limit=max(edge_limit, safe_limit * 500))
        edge_by_id = {str(edge.get("id") or ""): edge for edge in [*edges, *central_edges]}
        edges = list(edge_by_id.values())
        node_by_id = {str(node.get("id") or ""): node for node in nodes}
        commit_nodes = [
            node
            for node in nodes
            if str(node.get("kind") or "") == "GitCommit"
            and _matches_version_flow_filter(node, commit=commit, session_id=session_id)
        ][:safe_limit]
        if commit and not commit_nodes:
            commit_nodes = [
                node
                for node in nodes
                if str(node.get("kind") or "") == "GitCommit" and _matches_commit(node, commit)
            ][:safe_limit]

        flows = [
            _build_version_flow(commit_node=commit_node, nodes=nodes, edges=edges, node_by_id=node_by_id)
            for commit_node in commit_nodes
        ]
        if not flows:
            flows = _build_central_version_flows(
                nodes=nodes,
                edges=edges,
                node_by_id=node_by_id,
                commit=commit,
                session_id=session_id,
                limit=safe_limit,
            )
        visible_node_ids: set[str] = set()
        visible_edge_ids: set[str] = set()
        for flow in flows:
            visible_node_ids.update(str(node.get("id") or "") for node in flow.get("nodes", []))
            visible_edge_ids.update(str(edge.get("id") or "") for edge in flow.get("edges", []))

        visible_nodes = [node for node in nodes if str(node.get("id") or "") in visible_node_ids]
        visible_edges = [edge for edge in edges if str(edge.get("id") or "") in visible_edge_ids]
        return {
            "ok": True,
            "commit": commit,
            "session_id": session_id,
            "repo_id": safe_repo_id,
            "count": len(flows),
            "flows": flows,
            "nodes": visible_nodes,
            "edges": visible_edges,
            "warnings": _version_flow_warnings(flows),
        }

    def rebuild_retrieval_index(
        self,
        *,
        db_path: Path | None = None,
        session_id: str = "",
        repo_id: str = "",
        limit: int = 10000,
        max_doc_chars: int = 5000,
    ) -> dict[str, Any]:
        return _rebuild_retrieval_index(
            settings=self.settings,
            graph_store=self.store,
            db_path=db_path,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            max_doc_chars=max_doc_chars,
        )

    def embed_retrieval_index(
        self,
        *,
        db_path: Path | None = None,
        session_id: str = "",
        repo_id: str = "",
        limit: int = 0,
        model: str = "",
        graph_scope: str = "",
        rebuild_faiss: bool = True,
    ) -> dict[str, Any]:
        return _embed_retrieval_index(
            settings=self.settings,
            db_path=db_path,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            model=model,
            graph_scope=graph_scope,
            rebuild_faiss=rebuild_faiss,
        )

    def retrieve_indexed_graph(
        self,
        *,
        query: str,
        db_path: Path | None = None,
        session_id: str = "",
        repo_id: str = "",
        limit: int = 8,
        use_vector: bool = True,
        model: str = "",
        graph_scope: str = "",
        require_vector: bool = False,
        include_answer: bool = True,
    ) -> dict[str, Any]:
        return _retrieve_indexed_graph(
            settings=self.settings,
            graph_store=self.store,
            query=query,
            db_path=db_path,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            use_vector=use_vector,
            model=model,
            graph_scope=graph_scope,
            require_vector=require_vector,
            include_answer=include_answer,
        )

    def work_trace(self, *, commit: str = "HEAD", cwd: str | Path | None = None) -> dict[str, Any]:
        trace = WorkLedger(self.version_backend).trace_commit(commit=commit, cwd=cwd)
        return {"ok": trace.commit.available, "trace": trace.as_dict()}

    def _new_drain(self) -> EvidenceDrain:
        return EvidenceDrain(
            self.settings,
            evidence_roots=_evidence_roots(self.settings),
        )

def create_graph_service(settings: Settings) -> GraphRagService:
    return GraphRagService(settings)
