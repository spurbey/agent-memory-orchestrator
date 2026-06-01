from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ..core.db import connect
from ..integrations.adapters import normalize_adapter_event
from ..core.config import Settings
from ..evidence.drain import EvidenceDrain
from ..evidence.raw_store import RawEvidenceRef, RawEvidenceStore
from ..llm.embeddings import embed_text
from ..llm.qwen import DeterministicPlanner, OllamaQwenClient, QwenPlanner, QwenUnavailable
from ..infrastructure.faiss.embedding_store import GraphEmbeddingStore
from ..domain.versioning.repo_identity import resolve_repo_identity
from ..infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from ..infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ..application.services.retrieval_embedding import RETRIEVAL_EMBEDDING_KIND
from ..domain.retrieval.projection import build_retrieval_documents_from_graph
from ..application.services.retrieval_embedding import embed_missing_retrieval_documents
from ..application.services.retrieval_query import retrieve_session_graph as retrieve_indexed_session_graph
from ..infrastructure.llm.text_embedder import StrictTextEmbedder
from ..versioning import LocalGitBackend, VersionBackend, WorkLedger
from .answer_context import _answer_from_retrieval_result
from .answer_context import _unique_nonempty as _unique_nonempty
from .version_flow import _build_central_version_flows
from .version_flow import _build_version_flow
from .version_flow import _is_central_graph_seed
from .version_flow import _is_isolated_graph_seed
from .version_flow import _isolated_graph_seed_pool
from .version_flow import _matches_commit
from .version_flow import _matches_version_flow_filter
from .version_flow import _version_flow_warnings
from .central_trace import _active_central_versions_for_support as _active_central_versions_for_support
from .central_trace import _central_answer_trace_from_retrieval
from .constants import ANSWER_SEED_KINDS
from .constants import CAPTURE_ONLY_EVENTS
from .constants import HOOK_CONTEXT_EVENTS
from .retrieval_policy import _apply_retrieval_policy
from .retrieval_policy import _expand_nodes
from .retrieval_policy import _filter_answer_grade_nodes
from .retrieval_policy import _kinds_for_intent
from .retrieval_policy import _rank_nodes
from .retrieval_policy import _sanitize_output_node
from .retrieval_policy import _seed_kinds_for_retrieval
from .retrieval_policy import _trim_weak_tail_matches
from ..application.services.session_detail import build_session_detail_fallback as build_session_detail_fallback
from ..application.services.session_detail import _evidence_roots
from ..application.services.session_detail import _load_evidence_records
from ..application.services.session_detail import _load_session_evidence_records as _load_session_evidence_records
from ..application.services.session_detail import _reconstruct_clean_windows
from ..application.services.session_detail import _session_pending_summary as _session_pending_summary
from ..application.services.session_detail import _timeline_row
from .store import GraphEdge, GraphNode, GraphStore, KuzuGraphStore


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
        normalized = normalize_adapter_event(payload, default_agent=default_agent) or _fallback_event(payload, default_agent)
        session_id = str(normalized["session_id"])
        event_type = str(normalized["event_type"])
        source_app = str(normalized["source_app"])
        evidence = self.evidence.append(payload, session_id=session_id, source_app=source_app, event_name=event_type)
        cwd = _event_cwd(payload, normalized)
        git = self.version_backend.snapshot(cwd)

        git_raw = git.as_dict()
        git_compact = _compact_git(git_raw)
        self._upsert_basic_nodes(normalized, evidence=evidence, git=git_compact)
        merge = self._auto_merge_if_commit_event(normalized, evidence=evidence, git=git_raw)

        context = ""
        if event_type in HOOK_CONTEXT_EVENTS:
            context = self.startup_context(session_id=session_id, source_app=source_app, git=git_compact)
        return {
            "ok": True,
            "session_id": session_id,
            "event_type": event_type,
            "source_app": source_app,
            "evidence": evidence.as_dict(),
            "git": git_compact,
            "merge": merge,
            "additional_context": context,
            "capture_only": event_type in CAPTURE_ONLY_EVENTS,
        }

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
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        started = time.monotonic()
        timings: dict[str, int] = {}
        qwen_status: dict[str, Any] = {
            "planner_fallback": False,
            "compression_fallback": False,
            "planner_error": "",
            "compression_error": "",
        }
        try:
            plan_started = time.monotonic()
            plan = self.planner.plan_query(query)
            timings["planner_ms"] = _elapsed_ms(plan_started)
        except QwenUnavailable as exc:
            timings["planner_ms"] = _elapsed_ms(plan_started)
            qwen_status["planner_fallback"] = True
            qwen_status["planner_error"] = str(exc)
            plan = DeterministicPlanner().plan_query(query)
        plan = _apply_retrieval_policy(query=query, plan=plan, include_raw=include_raw)
        raw_requested = bool(plan.include_raw)
        kinds = _seed_kinds_for_retrieval(_kinds_for_intent(plan.intent), include_raw=raw_requested)
        search_started = time.monotonic()
        search_limit = max(limit * 12, 80)
        seed_nodes = self.store.search_nodes(query, limit=search_limit, kinds=kinds)
        expanded = _filter_answer_grade_nodes(_expand_nodes(seed_nodes, self.store), include_raw=raw_requested)
        candidates = _rank_nodes(query, expanded, include_historical=include_historical or plan.include_historical)
        if not candidates and not raw_requested:
            fallback_nodes = self.store.list_nodes(kinds=kinds or ANSWER_SEED_KINDS, limit=max(limit * 20, 120))
            fallback_filtered = _filter_answer_grade_nodes(fallback_nodes, include_raw=False)
            candidates = _rank_nodes(
                query,
                fallback_filtered,
                include_historical=include_historical or plan.include_historical,
                require_lexical=True,
            )
        candidates = _trim_weak_tail_matches(candidates)
        selected = [_sanitize_output_node(node) for node in candidates[: max(1, min(50, int(limit)))]]
        timings["retrieval_ms"] = _elapsed_ms(search_started)
        if selected:
            try:
                compression_started = time.monotonic()
                context = self.planner.compress_context(
                    query=query,
                    nodes=selected,
                    include_raw=raw_requested,
                )
                timings["compression_ms"] = _elapsed_ms(compression_started)
            except QwenUnavailable as exc:
                timings["compression_ms"] = _elapsed_ms(compression_started)
                qwen_status["compression_fallback"] = True
                qwen_status["compression_error"] = str(exc)
                context = DeterministicPlanner().compress_context(
                    query=query,
                    nodes=selected,
                    include_raw=raw_requested,
                )
        else:
            context = (
                "AMO GraphRAG context.\n"
                "No answer-grade graph memory matched this query. "
                "Raw evidence is available only through explicit raw-evidence retrieval."
            )
        return {
            "ok": True,
            "query": query,
            "plan": plan.as_dict(),
            "count": len(selected),
            "context": context,
            "nodes": selected,
            "raw_included": raw_requested,
            "qwen": qwen_status,
            "timing": {**timings, "total_ms": _elapsed_ms(started)},
        }

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
        safe_limit = max(1, min(100, int(limit)))
        safe_repo_id = str(repo_id or "").strip()
        records = _load_evidence_records(_evidence_roots(self.settings), limit=5000)
        jobs_by_session = self._jobs_by_session(limit=5000)
        repo_cache: dict[str, str] = {}
        sessions: dict[str, dict[str, Any]] = {}
        for record in records:
            session_id = str(record.get("session_id") or "default")
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            row = sessions.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "source_apps": set(),
                    "raw_events": 0,
                    "event_counts": {},
                    "latest_at": "",
                    "first_at": "",
                    "cwd": "",
                    "repo": "",
                    "repo_id": "",
                    "branch": "",
                    "latest_event": "",
                },
            )
            row["raw_events"] += 1
            row["source_apps"].add(str(record.get("source_app") or "unknown"))
            event_name = str(record.get("event_name") or "message")
            row["event_counts"][event_name] = int(row["event_counts"].get(event_name, 0)) + 1
            created_at = str(record.get("created_at") or "")
            if not row["first_at"] or created_at < row["first_at"]:
                row["first_at"] = created_at
            if not row["latest_at"] or created_at >= row["latest_at"]:
                row["latest_at"] = created_at
                row["latest_event"] = event_name
                row["cwd"] = str(payload.get("cwd") or row.get("cwd") or "")
                git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
                row["repo"] = str(git.get("repo_root") or payload.get("repo_root") or row.get("repo") or "")
                row["repo_id"] = _repo_id_for_path(row["repo"] or row["cwd"], repo_cache)
                row["branch"] = str(git.get("branch") or row.get("branch") or "")

        contexts: dict[str, dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        for session_id, row in sessions.items():
            job = jobs_by_session.get(session_id, {})
            effective_repo_id = str(job.get("repo_id") or row.get("repo_id") or "")
            if not effective_repo_id:
                effective_repo_id = _repo_id_for_path(str(row.get("repo") or row.get("cwd") or ""), repo_cache)
            if safe_repo_id and effective_repo_id != safe_repo_id:
                continue
            context = contexts.get(session_id)
            counts = self.store.merge_status(session_id=session_id).get("counts", {})
            rows.append(
                {
                    **{key: value for key, value in row.items() if key != "source_apps"},
                    "repo_id": effective_repo_id,
                    "repo_path": str(job.get("repo_path") or row.get("repo") or row.get("cwd") or ""),
                    "source_apps": sorted(row["source_apps"]),
                    "graph_counts": counts,
                    "latest_context": context,
                }
            )
        rows.sort(key=lambda item: str(item.get("latest_at") or ""), reverse=True)
        return {
            "ok": True,
            "repo_id": safe_repo_id,
            "graph_status": self.merge_status(),
            "sessions": rows[:safe_limit],
        }

    def list_repositories(self, *, limit: int = 200) -> dict[str, Any]:
        repos: dict[str, dict[str, Any]] = {}

        def add(repo_id: str, repo_path: str = "", *, source: str = "", updated_at: str = "", job_count: int = 0, plan_count: int = 0) -> None:
            key = str(repo_id or "").strip()
            if not key:
                return
            row = repos.setdefault(
                key,
                {"repo_id": key, "repo_path": "", "sources": set(), "job_count": 0, "plan_count": 0, "node_count": 0, "updated_at": ""},
            )
            if repo_path and not row["repo_path"]:
                row["repo_path"] = repo_path
            if source:
                row["sources"].add(source)
            row["job_count"] += int(job_count)
            row["plan_count"] += int(plan_count)
            if updated_at and updated_at > str(row["updated_at"]):
                row["updated_at"] = updated_at

        job_store = ProductionSessionJobStore(self.settings)
        try:
            for repo in job_store.list_repositories(limit=limit):
                add(
                    str(repo.get("repo_id") or ""),
                    str(repo.get("repo_path") or ""),
                    source="jobs",
                    updated_at=str(repo.get("updated_at") or ""),
                    job_count=int(repo.get("job_count") or 0),
                    plan_count=int(repo.get("plan_count") or 0),
                )
        finally:
            job_store.close()
        for node in self.store.list_nodes(limit=10000, kinds=["KnowledgeAtom", "KnowledgeVersion", "GraphView"]):
            node_repo_id = _node_repo_id(node)
            if not node_repo_id:
                continue
            add(node_repo_id, _node_repo_path(node), source="central_graph")
            repos[node_repo_id]["node_count"] += 1
        out = []
        for row in repos.values():
            out.append({**row, "sources": sorted(row["sources"])})
        out.sort(key=lambda item: (str(item.get("updated_at") or ""), int(item.get("node_count") or 0)), reverse=True)
        return {"ok": True, "repos": out[: max(1, int(limit))]}

    def _jobs_by_session(self, *, limit: int = 5000) -> dict[str, dict[str, Any]]:
        job_store = ProductionSessionJobStore(self.settings)
        try:
            return {str(job.get("session_id") or ""): job for job in job_store.list_jobs(limit=limit) if job.get("session_id")}
        finally:
            job_store.close()

    def session_detail(self, *, session_id: str, limit: int = 120) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        safe_limit = max(1, min(500, int(limit)))
        records, evidence_source = _load_session_evidence_records(self.settings, session_id=session_id, limit=safe_limit)
        nodes = [_sanitize_output_node(node) for node in self.store.list_nodes(session_id=session_id, limit=300)]
        edges = self.store.list_edges(session_id=session_id, limit=500)
        pending = _session_pending_summary(self.settings, session_id=session_id)
        windows = _reconstruct_clean_windows(records, nodes)
        return {
            "ok": True,
            "session_id": session_id,
            "timeline": [_timeline_row(record) for record in records],
            "windows": windows,
            "current_context": self.current_context(session_id=session_id, limit=5),
            "merge_status": self.merge_status(session_id=session_id),
            "pending": {"count": pending.get("count", 0), "cursor_path": pending.get("cursor_path"), "source": pending.get("source")},
            "evidence_source": evidence_source,
            "graph": {
                "nodes": nodes,
                "edges": edges,
            },
            "central_graph": self.central_graph(limit=80),
        }

    def central_graph(self, *, limit: int = 100, full: bool = False, repo_id: str = "") -> dict[str, Any]:
        max_limit = 10000 if full else 500
        safe_limit = max(1, min(max_limit, int(limit)))
        safe_repo_id = str(repo_id or "").strip()
        all_nodes = [
            node
            for node in self.store.list_nodes(limit=safe_limit * 8)
            if _matches_repo_scope(node, safe_repo_id)
        ]
        pool = [
            *[node for node in self.store.list_nodes(status="committed", limit=safe_limit) if _matches_repo_scope(node, safe_repo_id)],
            *[node for node in self.store.list_nodes(status="active", limit=safe_limit) if _matches_repo_scope(node, safe_repo_id)],
            *all_nodes,
        ]
        output_ids: set[str] = set()
        nodes: list[dict[str, Any]] = []
        isolated_pool: list[dict[str, Any]] = []
        for node in pool:
            node_id = str(node.get("id") or "")
            if node_id in output_ids:
                continue
            if _is_central_graph_seed(node):
                nodes.append(_sanitize_output_node(node))
                output_ids.add(node_id)
            if len(nodes) >= safe_limit:
                break
        if not nodes:
            isolated_pool = _isolated_graph_seed_pool(self.store, all_nodes, limit=safe_limit)
            for node in isolated_pool:
                node_id = str(node.get("id") or "")
                if node_id in output_ids:
                    continue
                if not _is_isolated_graph_seed(node):
                    continue
                nodes.append(_sanitize_output_node(node))
                output_ids.add(node_id)
                if len(nodes) >= safe_limit:
                    break
        node_by_id = {str(node.get("id") or ""): node for node in all_nodes + pool + isolated_pool}
        all_edges = self.store.list_edges(limit=safe_limit * 32)
        central_edges: list[dict[str, Any]] = []
        edge_ids: set[str] = set()
        frontier = set(output_ids)
        for _depth in range(3):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for edge in all_edges:
                edge_id = str(edge.get("id") or "")
                source_id = str(edge.get("source_id") or "")
                target_id = str(edge.get("target_id") or "")
                if not source_id or not target_id:
                    continue
                if source_id not in frontier and target_id not in frontier:
                    continue
                missing_endpoint_ids = [
                    endpoint_id for endpoint_id in (source_id, target_id) if endpoint_id not in output_ids
                ]
                if len(nodes) + len(missing_endpoint_ids) > safe_limit:
                    continue
                missing_endpoints: list[tuple[str, dict[str, Any]]] = []
                for endpoint_id in missing_endpoint_ids:
                    endpoint = node_by_id.get(endpoint_id)
                    if not endpoint:
                        missing_endpoints = []
                        break
                    missing_endpoints.append((endpoint_id, endpoint))
                if len(missing_endpoints) != len(missing_endpoint_ids):
                    continue
                if edge_id not in edge_ids:
                    central_edges.append(edge)
                    edge_ids.add(edge_id)
                for endpoint_id, endpoint in missing_endpoints:
                    nodes.append(_sanitize_output_node(endpoint))
                    output_ids.add(endpoint_id)
                    next_frontier.add(endpoint_id)
                if len(central_edges) >= safe_limit * 4:
                    break
            frontier = next_frontier
            if len(central_edges) >= safe_limit * 4:
                break
        return {
            "ok": True,
            "repo_id": safe_repo_id,
            "nodes": nodes,
            "edges": central_edges[: safe_limit * 4],
            "full": full,
            "limit": safe_limit,
            "status": self.merge_status(),
            "warnings": _central_graph_warnings(nodes, central_edges),
        }

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
            if _matches_repo_scope(node, safe_repo_id)
        ]
        central_nodes = [
            _sanitize_output_node(node)
            for node in self.store.list_nodes(
                kinds=["GraphCommit", "KnowledgeAtom", "KnowledgeVersion"],
                limit=max(node_limit, safe_limit * 300),
            )
            if _matches_repo_scope(node, safe_repo_id)
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
        target_db = _retrieval_db_path(self.settings, db_path)
        conn = connect(target_db)
        try:
            index = RetrievalIndexStore(conn)
            docs = build_retrieval_documents_from_graph(
                self.store,
                session_id=session_id,
                repo_id=repo_id,
                node_limit=max(1, min(100000, int(limit))),
                max_doc_chars=max(1000, int(max_doc_chars)),
            )
            written = index.replace_documents(docs)
            return {
                "ok": True,
                "db_path": str(target_db),
                "graph_path": str(self.settings.graph_path),
                "session_id": session_id,
                "repo_id": repo_id,
                "retrieval_document_count": written,
                "doc_type_counts": _count_by(docs, "doc_type"),
                "node_kind_counts": _count_by(docs, "node_kind"),
            }
        finally:
            conn.close()

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
        target_db = _retrieval_db_path(self.settings, db_path)
        embedding_model = model or self.settings.embedding_model
        scope = graph_scope or self.settings.retrieval_graph_scope or _graph_scope_for_path(self.settings.graph_path)
        conn = connect(target_db)
        try:
            index = RetrievalIndexStore(conn)
            embedding_store = GraphEmbeddingStore(conn, db_path=target_db)
            embedder = _text_embedder_for_model(embedding_model, dims=self.settings.embedding_dims)
            result = embed_missing_retrieval_documents(
                index_store=index,
                embedding_store=embedding_store,
                embedder=embedder,
                model=embedding_model,
                graph_scope=scope,
                session_id=session_id,
                repo_id=repo_id,
                extraction_run_id="graph_retrieval_index",
                limit=max(0, int(limit)),
            )
            faiss = (
                embedding_store.build_faiss_cache(
                    embedding_kind="retrieval_text",
                    model=embedding_model,
                    graph_scope=scope,
                ).as_dict()
                if rebuild_faiss
                else {"status": "skipped", "reason": "disabled"}
            )
            return {
                "ok": True,
                "db_path": str(target_db),
                "graph_path": str(self.settings.graph_path),
                "graph_scope": scope,
                "repo_id": repo_id,
                "embedding": result.as_dict(),
                "faiss": faiss,
            }
        finally:
            conn.close()

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
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        target_db = _retrieval_db_path(self.settings, db_path)
        embedding_model = model or self.settings.embedding_model
        conn = connect(target_db)
        try:
            scope = _resolve_retrieval_graph_scope(
                conn,
                requested_scope=graph_scope or self.settings.retrieval_graph_scope,
                default_scope=_graph_scope_for_path(self.settings.graph_path),
                embedding_model=embedding_model,
            )
            index = RetrievalIndexStore(conn)
            if repo_id and not index.active_projection_id(repo_id):
                return {
                    "ok": False,
                    "error": "active_projection_missing",
                    "db_path": str(target_db),
                    "graph_path": str(self.settings.graph_path),
                    "graph_scope": scope,
                    "repo_id": repo_id,
                    "retrieval": {
                        "query": query,
                        "hits": [],
                        "vector_status": "not_requested" if not use_vector else "unavailable",
                    },
                    "central_answer_trace": _central_answer_trace_from_retrieval(
                        self.settings,
                        repo_id=repo_id,
                        retrieval={"hits": []},
                        graph_store=self.store,
                        warnings=["active_projection_missing"],
                    ),
                }
            embedding_store: GraphEmbeddingStore | None = None
            embedder = None
            if use_vector and self.settings.vector_backend != "disabled":
                embedding_store = GraphEmbeddingStore(conn, db_path=target_db)
                embedder = _text_embedder_for_model(embedding_model, dims=self.settings.embedding_dims)
            result = retrieve_indexed_session_graph(
                query=query,
                index_store=index,
                graph_store=self.store,
                embedding_store=embedding_store,
                embedder=embedder,
                embedding_model=embedding_model if embedder is not None else "",
                graph_scope=scope,
                session_id=session_id,
                repo_id=repo_id,
                limit=max(1, min(50, int(limit))),
                expand_neighbors=12 if include_answer else 0,
                include_graph_nodes=include_answer,
                require_vector=require_vector,
                reranker_backend=self.settings.reranker_backend,
                reranker_model=self.settings.reranker_model,
                rerank_top_k=self.settings.rerank_top_k,
                rerank_max_chars=self.settings.rerank_max_chars,
            )
            payload = {
                "ok": True,
                "db_path": str(target_db),
                "graph_path": str(self.settings.graph_path),
                "graph_scope": scope,
                "repo_id": repo_id,
                "retrieval": result.as_dict(),
            }
            if repo_id:
                payload["central_answer_trace"] = _central_answer_trace_from_retrieval(
                    self.settings,
                    repo_id=repo_id,
                    retrieval=result.as_dict(),
                    graph_store=self.store,
                )
            if include_answer:
                payload["answer"] = _answer_from_retrieval_result(
                    result.as_dict(),
                    graph_store=self.store,
                    session_id=session_id,
                )
            return payload
        finally:
            conn.close()

    def work_trace(self, *, commit: str = "HEAD", cwd: str | Path | None = None) -> dict[str, Any]:
        trace = WorkLedger(self.version_backend).trace_commit(commit=commit, cwd=cwd)
        return {"ok": trace.commit.available, "trace": trace.as_dict()}

    def _new_drain(self) -> EvidenceDrain:
        return EvidenceDrain(
            self.settings,
            evidence_roots=_evidence_roots(self.settings),
        )

    def _upsert_basic_nodes(self, normalized: dict[str, Any], *, evidence: RawEvidenceRef, git: dict[str, Any]) -> None:
        session_id = str(normalized["session_id"])
        source_app = str(normalized["source_app"])
        event_type = str(normalized["event_type"])
        content = str(normalized["content"])
        metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
        session_node = GraphNode(
            id=f"session:{session_id}",
            kind="Session",
            label=session_id,
            summary=f"{source_app} session {session_id}",
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            metadata={"git": git},
        )
        app_node = GraphNode(
            id=f"app:{source_app}",
            kind="App",
            label=source_app,
            summary=f"Source app {source_app}",
            status="active",
            scope="central",
            source_app=source_app,
        )
        evidence_node = GraphNode(
            id=f"evidence:{evidence.id}",
            kind="RawEvidenceRef",
            label=evidence.id,
            summary=f"{event_type} raw evidence from {source_app}",
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=evidence.id,
            metadata=evidence.as_dict(),
        )
        event_node = GraphNode(
            id=f"event:{evidence.id}",
            kind=_node_kind_for_event(event_type),
            label=_label_for_event(event_type, content),
            summary=_summarize_event(event_type, content),
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=evidence.id,
            metadata={"event_type": event_type, **metadata, "git": git},
        )
        for node in (session_node, app_node, evidence_node, event_node):
            self.store.upsert_node(node)
        self._edge(session_node.id, app_node.id, "PART_OF", evidence.id)
        self._edge(session_node.id, event_node.id, "HAS_TURN", evidence.id)
        self._edge(event_node.id, evidence_node.id, "EVIDENCED_BY", evidence.id)

        if git.get("available"):
            repo_id = f"repo:{git.get('repo_root')}"
            branch_id = f"branch:{git.get('repo_root')}:{git.get('branch')}"
            self.store.upsert_node(
                GraphNode(
                    id=repo_id,
                    kind="Repo",
                    label=str(git.get("repo_root")),
                    summary=f"Local Git repo {git.get('repo_root')}",
                    status="active",
                    scope="central",
                    project_id=self.settings.project_id,
                    metadata=git,
                )
            )
            self.store.upsert_node(
                GraphNode(
                    id=branch_id,
                    kind="Branch",
                    label=str(git.get("branch")),
                    summary=f"Branch {git.get('branch')} in {git.get('repo_root')}",
                    status="active",
                    scope="central",
                    project_id=self.settings.project_id,
                    commit_id=str(git.get("head") or ""),
                    metadata=git,
                )
            )
            self._edge(session_node.id, repo_id, "PART_OF", evidence.id)
            self._edge(branch_id, repo_id, "PART_OF", evidence.id)

    def _auto_merge_if_commit_event(
        self,
        normalized: dict[str, Any],
        *,
        evidence: RawEvidenceRef,
        git: dict[str, Any],
    ) -> dict[str, Any]:
        if not git.get("available") or not git.get("head"):
            return {"merged": False, "reason": "git_unavailable"}
        content = str(normalized.get("content") or "")
        metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
        if not _looks_like_commit_event(content, metadata):
            return {"merged": False, "reason": "not_commit_event"}

        commit = str(git["head"])
        session_id = str(normalized["session_id"])
        commit_node = GraphNode(
            id=f"commit:{commit}",
            kind="GitCommit",
            label=commit[:12],
            summary=f"Git commit {commit[:12]} on {git.get('branch')}",
            status="committed",
            scope="central",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=str(normalized["source_app"]),
            evidence_id=evidence.id,
            commit_id=commit,
            metadata=git,
        )
        self.store.upsert_node(commit_node)
        self._edge(f"session:{session_id}", commit_node.id, "MERGED_INTO", evidence.id)
        self._edge(f"event:{evidence.id}", commit_node.id, "COMMITTED_AS", evidence.id)
        return {"merged": True, "commit": commit}

    def _edge(self, source: str, target: str, kind: str, evidence_id: str) -> None:
        self.store.upsert_edge(
            GraphEdge(
                id=f"edge:{source}:{kind}:{target}",
                source_id=source,
                target_id=target,
                kind=kind,
                evidence_id=evidence_id,
            )
        )


def create_graph_service(settings: Settings) -> GraphRagService:
    return GraphRagService(settings)


class _HashTextEmbedder:
    def __init__(self, dims: int) -> None:
        self.dims = dims

    def embed(self, text: str) -> list[float]:
        return embed_text(text, self.dims)


def _text_embedder_for_model(model: str, *, dims: int):
    if model.strip().lower() in {"hash", "hash-fallback", "deterministic", "local-hash"}:
        return _HashTextEmbedder(dims)
    return StrictTextEmbedder(model)


def _retrieval_db_path(settings: Settings, override: Path | None = None) -> Path:
    path = override or settings.retrieval_db_path or settings.db_path
    target = path if path.is_absolute() else (settings.home / path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _graph_scope_for_path(graph_path: Path) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", str(graph_path.resolve()).lower()).strip("_")
    return f"graph:{safe[-80:] or 'default'}"


def _resolve_retrieval_graph_scope(
    conn: Any,
    *,
    requested_scope: str,
    default_scope: str,
    embedding_model: str,
) -> str:
    requested = str(requested_scope or "").strip()
    if not embedding_model:
        return requested or default_scope

    if requested:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM graph_embeddings
            WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
            """,
            (RETRIEVAL_EMBEDDING_KIND, embedding_model, requested),
        ).fetchone()
        if int(row["count"] if row else 0) > 0:
            return requested

    params = (RETRIEVAL_EMBEDDING_KIND, embedding_model, default_scope)
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM graph_embeddings
        WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
        """,
        params,
    ).fetchone()
    if int(row["count"] if row else 0) > 0:
        return default_scope

    fallback = conn.execute(
        """
        SELECT graph_scope, COUNT(*) AS count
        FROM graph_embeddings
        WHERE embedding_kind = ? AND model = ? AND status = 'active'
        GROUP BY graph_scope
        ORDER BY count DESC, graph_scope ASC
        LIMIT 1
        """,
        (RETRIEVAL_EMBEDDING_KIND, embedding_model),
    ).fetchone()
    return str(fallback["graph_scope"]) if fallback else requested or default_scope


def _count_by(items: list[Any], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, attr, "") or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _fallback_event(payload: dict[str, Any], default_agent: str) -> dict[str, Any]:
    event_name = _snake(str(payload.get("hook_event_name") or payload.get("event_type") or "message"))
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "default")
    content = payload.get("prompt") or payload.get("content") or payload.get("message") or payload
    return {
        "session_id": session_id,
        "agent": default_agent,
        "event_type": event_name,
        "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True),
        "metadata": {},
        "created_at": payload.get("created_at") or payload.get("timestamp"),
        "source_app": default_agent,
    }


def _event_cwd(payload: dict[str, Any], normalized: dict[str, Any]) -> str | Path | None:
    metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
    return payload.get("cwd") or metadata.get("cwd") or os.getenv("AMO_WORKSPACE_CWD") or None


def _repo_id_for_path(path: str | Path, cache: dict[str, str]) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if text not in cache:
        cache[text] = resolve_repo_identity(text).repo_id
    return cache[text]


def _node_repo_id(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("repo_id") or metadata.get("repo_id") or "")


def _node_repo_path(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("repo_path") or metadata.get("repo_path") or metadata.get("repo_root") or "")


def _matches_repo_scope(node: dict[str, Any], repo_id: str) -> bool:
    if not repo_id:
        return True
    return _node_repo_id(node) == repo_id


def _node_kind_for_event(event_type: str) -> str:
    if event_type in {"prompt", "user_prompt_submit"}:
        return "Prompt"
    if "tool" in event_type:
        return "ToolResult"
    if "response" in event_type:
        return "Response"
    return "Turn"


def _label_for_event(event_type: str, content: str) -> str:
    first = " ".join(content.strip().split())[:96]
    return first or event_type


def _summarize_event(event_type: str, content: str) -> str:
    clean = " ".join(str(content or "").split())
    if len(clean) > 360:
        clean = clean[:357] + "..."
    return f"{event_type}: {clean}" if clean else event_type


def _looks_like_commit_event(content: str, metadata: dict[str, Any]) -> bool:
    lowered = f"{content}\n{json.dumps(metadata, sort_keys=True)}".lower()
    return "git commit" in lowered or bool(re.search(r"\[[^\]]+ [0-9a-f]{7,}\]", lowered))


def _central_graph_warnings(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    node_ids = {str(node.get("id") or "") for node in nodes}
    dangling = [
        edge
        for edge in edges
        if str(edge.get("source_id") or "") not in node_ids or str(edge.get("target_id") or "") not in node_ids
    ]
    if nodes and len(edges) < max(1, len(nodes) // 5):
        warnings.append("central_graph_edges_sparse")
    if dangling:
        warnings.append("central_graph_has_dangling_visible_edges")
    version_edges = [
        edge
        for edge in edges
        if edge.get("kind")
        in {
            "COMMITTED_AS",
            "REFINES",
            "SUPERSEDES",
            "DUPLICATE_OF",
            "CONTRADICTS",
            "REASON_NODE_EXPLAINS_COMMIT",
            "REASON_NODE_IN_PACKET",
            "COMMIT_PRODUCED_HUNK",
        }
    ]
    if nodes and not version_edges:
        warnings.append("central_graph_has_no_visible_version_edges")
    return warnings


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def _compact_git(git: dict[str, Any]) -> dict[str, Any]:
    changed = [str(path) for path in git.get("changed_files", []) if path]
    staged = [str(path) for path in git.get("staged_files", []) if path]
    return {
        "available": bool(git.get("available")),
        "repo_root": str(git.get("repo_root") or ""),
        "branch": str(git.get("branch") or ""),
        "head": str(git.get("head") or ""),
        "dirty": bool(git.get("dirty")),
        "changed_count": len(changed),
        "staged_count": len(staged),
        "changed_files": changed[:20],
        "staged_files": staged[:20],
        "error": str(git.get("error") or ""),
    }


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
