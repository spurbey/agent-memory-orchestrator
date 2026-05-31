from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from typing import Iterable

from ..core.db import connect
from ..integrations.adapters import normalize_adapter_event
from ..core.config import Settings
from ..evidence.drain import EvidenceDrain
from ..evidence.drain import _read_jsonl_from
from ..evidence.raw_store import RawEvidenceRef, RawEvidenceStore
from ..evidence.triggers import TriggerDecision
from ..evidence.triggers import is_session_start
from ..evidence.triggers import record_session_id
from ..evidence.triggers import session_boundary_trigger
from ..evidence.window import clean_evidence_window
from ..llm.embeddings import embed_text
from ..llm.qwen import DeterministicPlanner, OllamaQwenClient, QueryPlan, QwenPlanner, QwenUnavailable
from ..reasoning_graph.embedding_store import GraphEmbeddingStore
from ..domain.versioning.repo_identity import resolve_repo_identity
from ..infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from ..infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ..reasoning_graph.retrieval import RETRIEVAL_EMBEDDING_KIND
from ..reasoning_graph.retrieval import build_retrieval_documents_from_graph
from ..reasoning_graph.retrieval import embed_missing_retrieval_documents
from ..reasoning_graph.retrieval import retrieve_session_graph as retrieve_indexed_session_graph
from ..reasoning_graph.session_runtime import StrictTextEmbedder
from ..versioning import LocalGitBackend, VersionBackend, WorkLedger
from .answer_trace import build_answer_trace
from .answer_trace import build_central_answer_trace
from .store import GraphEdge, GraphNode, GraphStore, KuzuGraphStore


HOOK_CONTEXT_EVENTS = {"session_start"}
CAPTURE_ONLY_EVENTS = {"user_prompt_submit", "prompt", "post_tool_use", "tool_result", "stop", "session_stop"}
EVIDENCE_ONLY_KINDS = {"RawEvidenceRef", "Prompt", "ToolUse", "ToolResult", "Turn", "Session", "App", "Repo", "Branch"}
SUPPORT_ONLY_KINDS = {"File", "Symbol", "Topic", "CleanedEvidenceWindow"}
ANSWER_SEED_KINDS = [
    "ReasoningNode",
    "DecisionUnit",
    "Decision",
    "WorkChange",
    "Fix",
    "Bug",
    "Blocker",
    "TestRun",
    "GitCommit",
    "Commit",
    "KnowledgeAtom",
    "KnowledgeVersion",
    "CodeNode",
    "Symbol",
]
ISOLATED_GRAPH_VISUAL_KINDS = {
    "ReasoningNode",
    "DecisionUnit",
    "Problem",
    "Decision",
    "Cause",
    "Fix",
    "Constraint",
    "OpenQuestion",
    "WorkChange",
    "Commit",
    "GitCommit",
    "Packet",
    "CodeNode",
    "CodeVersion",
    "CodeHunk",
    "Symbol",
    "EvidenceRef",
}
ISOLATED_GRAPH_VISUAL_STATUSES = {
    "session_final",
    "candidate_reasoning_packet",
    "accepted",
    "active",
    "committed",
}
VERSION_FLOW_EDGE_KINDS = {
    "COMMITTED_AS",
    "REFINES",
    "SUPERSEDES",
    "DUPLICATE_OF",
    "CONTRADICTS",
    "VALIDATED_BY",
    "MODIFIES",
    "MERGED_INTO",
    "EVIDENCED_BY",
    "CLEANED_INTO",
    "EXTRACTED_AS",
    "CREATED",
    "PRODUCED",
    "HAS_WINDOW",
}
VERSION_RELATION_EDGE_KINDS = {"REFINES", "SUPERSEDES", "DUPLICATE_OF", "CONTRADICTS"}
RETRIEVAL_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "but",
    "can",
    "could",
    "did",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "into",
    "its",
    "not",
    "now",
    "only",
    "should",
    "that",
    "the",
    "then",
    "this",
    "use",
    "used",
    "using",
    "via",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "why",
    "will",
    "with",
    "would",
}


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


def _answer_from_retrieval_result(
    result: dict[str, Any],
    *,
    graph_store: GraphStore | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    hits = result.get("hits") if isinstance(result.get("hits"), list) else []
    if not hits:
        return {
            "text": "No indexed graph evidence matched the query.",
            "citations": [],
            "node_ids": [],
        }
    citations: list[dict[str, Any]] = []
    node_ids: list[str] = []
    context_items: list[dict[str, Any]] = []
    for index, hit in enumerate(hits[:20], start=1):
        doc = hit.get("document") if isinstance(hit, dict) and isinstance(hit.get("document"), dict) else {}
        graph_node = hit.get("graph_node") if isinstance(hit, dict) and isinstance(hit.get("graph_node"), dict) else {}
        neighbors = hit.get("neighbors") if isinstance(hit, dict) and isinstance(hit.get("neighbors"), list) else []
        node_id = str(doc.get("graph_node_id") or graph_node.get("id") or "")
        node_ids.append(node_id)
        title = _public_answer_title(doc=doc, graph_node=graph_node, fallback=node_id)
        body = str(doc.get("body") or graph_node.get("summary") or "")
        statement = _public_answer_statement(doc=doc, graph_node=graph_node, body=body)
        reason = _body_field(body, "reason")
        trace = (
            build_answer_trace(
                seed_node_id=node_id,
                graph_store=graph_store,
                query=str(result.get("query") or ""),
                session_id=session_id,
            )
            if graph_store is not None and node_id
            else {}
        )
        support = _answer_support(doc=doc, graph_node=graph_node, neighbors=neighbors, trace=trace)
        if not trace.get("node_count"):
            trace = _fallback_trace_from_retrieval_doc(doc=doc, node_id=node_id, support=support)
        context_items.append(
            {
                "rank": index,
                "doc_type": str(doc.get("doc_type") or ""),
                "node_kind": str(doc.get("node_kind") or graph_node.get("kind") or ""),
                "packet_id": str(doc.get("packet_id") or ""),
                "commit_sha": str(doc.get("commit_sha") or ""),
                "title": title,
                "statement": statement,
                "reason": _public_answer_text(reason),
                "body": body,
                "metadata": doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {},
                "support": support,
                "trace": trace,
                "score": hit.get("score"),
            }
        )
        if index <= 8:
            citations.append(
                {
                    "rank": index,
                    "doc_id": doc.get("doc_id"),
                    "graph_node_id": node_id,
                    "doc_type": doc.get("doc_type"),
                    "packet_id": doc.get("packet_id"),
                    "commit_sha": doc.get("commit_sha"),
                    "packet_ids": support["packet_ids"],
                    "commit_shas": support["commit_shas"],
                    "evidence_ids": support["evidence_ids"],
                    "code_node_ids": support["code_node_ids"],
                    "code_nodes": support["code_nodes"],
                    "neighbor_node_ids": support["neighbor_node_ids"],
                    "trace": trace,
                    "score": hit.get("score"),
                }
            )
    return {
        "text": _render_retrieval_context_answer(query=str(result.get("query") or ""), items=context_items),
        "citations": citations,
        "node_ids": [node_id for node_id in node_ids if node_id],
        "context": _retrieval_context_payload(query=str(result.get("query") or ""), items=context_items),
    }


def _render_retrieval_context_answer(*, query: str, items: list[dict[str, Any]]) -> str:
    focused_items = _focused_context_items(query=query, items=items)
    if not focused_items:
        return "No indexed graph evidence matched the query."
    lines = ["Answer from repository memory:", "Use this as retrieval context for synthesis, not final prose.", f"Query: {query}"]
    version_timeline = _version_timeline_context(query=query, items=items, focused_items=focused_items)
    if version_timeline.get("entries"):
        target = ", ".join(version_timeline.get("target_paths") or [])
        heading = f"Version history for {target}:" if target else "Version history:"
        lines.extend(["", heading])
        for idx, entry in enumerate(version_timeline["entries"][:6], start=1):
            commit = str(entry.get("commit_sha") or "").strip()
            message = str(entry.get("message") or "").strip()
            label = " ".join(part for part in (commit[:7], message) if part).strip() or "versioned change"
            lines.append(f"{idx}. {label}")
            why = str(entry.get("why") or "").strip()
            if why:
                lines.append(f"   Why: {_clip(why, 260)}")
            files = entry.get("files") if isinstance(entry.get("files"), list) else []
            if files:
                lines.append("   Files: " + ", ".join(str(path) for path in files[:4]))
            support = entry.get("support") if isinstance(entry.get("support"), list) else []
            if support:
                lines.append("   Support: " + ", ".join(str(value) for value in support[:5]))
    discussion = _context_bucket(focused_items, {"packet"})
    reasoning = _context_bucket(focused_items, {"central_version", "reasoning"})
    implementation = _context_bucket(focused_items, {"code_impact", "file_impact", "file_ref", "symbol_ref", "code_region_ref"})

    if discussion:
        lines.extend(["", "Relevant work and discussion:"])
        for idx, item in enumerate(discussion[:3], start=1):
            lines.append(f"{idx}. {_context_line(item)}")
    if reasoning:
        lines.extend(["", "Decisions and reasoning:"])
        for idx, item in enumerate(reasoning[:4], start=1):
            lines.append(f"{idx}. {_context_line(item)}")
    if implementation:
        lines.extend(["", "Code and file support:"])
        for idx, item in enumerate(implementation[:5], start=1):
            lines.append(f"{idx}. {_context_line(item)}")

    support = _merged_support(focused_items)
    if version_timeline.get("entries"):
        support = _merge_public_support(support, _support_from_version_timeline(version_timeline))
    support_summary = _public_support_summary(support)
    trace_parts: list[str] = []
    if support.get("packet_ids"):
        trace_parts.append(f"packets={len(support['packet_ids'])}")
    if support.get("commit_shas"):
        trace_parts.append("commits=" + ", ".join(support["commit_shas"][:5]))
    if support.get("evidence_ids"):
        trace_parts.append(f"evidence={len(support['evidence_ids'])}")
    if support.get("code_nodes") or support.get("code_node_ids"):
        trace_parts.append(f"code_refs={len(support.get('code_nodes') or support.get('code_node_ids') or [])}")
    if support_summary or trace_parts:
        lines.extend(["", "Trace support:"])
        if support_summary:
            lines.append(f"Support: {support_summary}")
        if trace_parts:
            lines.append("Evidence: " + "; ".join(trace_parts))
    return "\n".join(lines)


def _retrieval_context_payload(*, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    focused_items = _focused_context_items(query=query, items=items)
    return {
        "query": query,
        "version_timeline": _version_timeline_context(query=query, items=items, focused_items=focused_items),
        "items": [
            {
                "rank": item.get("rank"),
                "doc_type": item.get("doc_type"),
                "node_kind": item.get("node_kind"),
                "title": item.get("title"),
                "statement": item.get("statement"),
                "reason": item.get("reason"),
                "score": item.get("score"),
                "support": item.get("support"),
            }
            for item in focused_items
        ],
        "support": _merged_support(focused_items),
    }


def _version_timeline_context(
    *,
    query: str,
    items: list[dict[str, Any]],
    focused_items: list[dict[str, Any]],
) -> dict[str, Any]:
    target_paths = _timeline_target_paths(query=query, items=[*focused_items, *items])
    if not target_paths:
        return {}
    matching = [item for item in items if _timeline_item_matches_paths(item, target_paths)]
    if not matching:
        return {}
    commit_count = len({commit for item in matching for commit in _timeline_item_commits(item)})
    if not _version_timeline_requested(query) and commit_count < 2:
        return {}

    order: list[str] = []
    entries: dict[str, dict[str, Any]] = {}
    for item in matching:
        _merge_timeline_item(entries=entries, order=order, item=item, target_paths=target_paths)

    ordered_entries = [
        {field: value for field, value in entries[key].items() if not str(field).startswith("_")}
        for key in order
        if key in entries
    ]
    ordered_entries = [entry for entry in ordered_entries if entry.get("commit_sha") or entry.get("why") or entry.get("files")]
    ordered_entries = [entry for entry in ordered_entries if _timeline_entry_has_explanation(entry)]
    return {
        "target_paths": target_paths,
        "entries": ordered_entries[:8],
        "commit_count": len({str(entry.get("commit_sha") or "") for entry in ordered_entries if entry.get("commit_sha")}),
    }


def _version_timeline_requested(query: str) -> bool:
    lowered = str(query or "").lower()
    return bool(
        re.search(
            r"\b(version history|version flow|version chain|versions?|over time|evolved?|evolution|history|current)\b",
            lowered,
        )
    )


def _timeline_entry_has_explanation(entry: dict[str, Any]) -> bool:
    support = {str(value or "") for value in entry.get("support", []) if str(value or "").strip()}
    why = str(entry.get("why") or "").strip()
    message = str(entry.get("message") or "").strip()
    files = {_normalize_public_path(path) for path in entry.get("files", []) if str(path or "").strip()}
    path_only_why = bool(why) and why in files
    path_only_message = message.startswith("File version:") or message.startswith("Commit version:")
    if support == {"central version"} and (not why or path_only_why or path_only_message):
        return False
    return True


def _timeline_target_paths(*, query: str, items: list[dict[str, Any]]) -> list[str]:
    locator_terms = _answer_code_locator_terms(query)
    if not locator_terms:
        return []
    query_lower = str(query or "").lower()
    scores: dict[str, float] = {}
    for item in items:
        doc_type = str(item.get("doc_type") or "")
        base = 0.0
        if doc_type == "file_impact":
            base = 3.0
        elif doc_type in {"code_impact", "central_version"}:
            base = 2.0
        elif doc_type in {"reasoning", "packet"}:
            base = 1.0
        for path in _timeline_item_paths(item):
            normalized = _normalize_public_path(path)
            if not normalized:
                continue
            path_lower = normalized.lower()
            basename = path_lower.rsplit("/", 1)[-1]
            score = base
            for term in locator_terms:
                term_lower = term.lower()
                if term_lower == path_lower or term_lower == basename:
                    score += 8.0
                elif term_lower in path_lower:
                    score += 3.0
                elif term_lower.replace(".py", "") and term_lower.replace(".py", "") in path_lower:
                    score += 1.5
            if "test" not in query_lower and "/test" not in path_lower and not basename.startswith("test_"):
                score += 0.75
            if score > base:
                scores[normalized] = max(scores.get(normalized, 0.0), score)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [path for path, _score in ranked[:1]]


def _timeline_item_matches_paths(item: dict[str, Any], target_paths: list[str]) -> bool:
    normalized_targets = {_normalize_public_path(path).lower() for path in target_paths if path}
    item_paths = {_normalize_public_path(path).lower() for path in _timeline_item_paths(item)}
    if normalized_targets.intersection(item_paths):
        return True
    text = _timeline_item_text(item).lower()
    for target in normalized_targets:
        basename = target.rsplit("/", 1)[-1]
        if target and target in text:
            return True
        if basename and basename in text:
            return True
    return False


def _merge_timeline_item(
    *,
    entries: dict[str, dict[str, Any]],
    order: list[str],
    item: dict[str, Any],
    target_paths: list[str],
) -> None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    doc_type = str(item.get("doc_type") or "")
    commits = _timeline_item_commits(item)
    if not commits:
        return
    messages = _unique_public_values(metadata.get("commit_messages"))
    packet_ids = _timeline_item_packets(item)
    evidence_ids = _timeline_item_evidence(item)
    files = [path for path in _timeline_item_paths(item) if path]
    reasons = _timeline_item_reasons(item)
    support_label = _timeline_support_label(doc_type)
    priority = _timeline_item_priority(item)

    for idx, commit in enumerate(commits):
        key = commit.lower()
        if key not in entries:
            entries[key] = {
                "commit_sha": commit,
                "message": "",
                "why": "",
                "files": [],
                "packets": [],
                "evidence": [],
                "support": [],
                "_message_priority": 0,
                "_why_priority": 0,
            }
            order.append(key)
        entry = entries[key]
        message = messages[idx] if idx < len(messages) else _timeline_message_from_item(item)
        if message and (not entry.get("message") or priority > int(entry.get("_message_priority") or 0)):
            entry["message"] = _public_answer_text(message)
            entry["_message_priority"] = priority
        reason = reasons[idx] if idx < len(reasons) else _timeline_reason_from_item(item, version_metadata)
        if reason and (not entry.get("why") or priority > int(entry.get("_why_priority") or 0)):
            entry["why"] = _public_answer_text(reason)
            entry["_why_priority"] = priority
        entry["files"] = _unique_public_values([entry.get("files"), files, target_paths])[:6]
        entry["packets"] = _unique_public_values([entry.get("packets"), packet_ids])[:6]
        entry["evidence"] = _unique_public_values([entry.get("evidence"), evidence_ids])[:8]
        support_parts = list(entry.get("support") or [])
        if support_label:
            support_parts.append(support_label)
        if packet_ids:
            support_parts.append("packet-backed")
        if evidence_ids:
            support_parts.append("evidence-backed")
        entry["support"] = _unique_public_values(support_parts)


def _timeline_item_commits(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    values: list[Any] = [
        item.get("commit_sha"),
        metadata.get("commit_sha"),
        metadata.get("source_commit_sha"),
        metadata.get("commit_shas"),
        version_metadata.get("producing_commit_sha"),
        version_metadata.get("linked_commits"),
    ]
    if str(node_metadata.get("atom_kind") or "") == "commit":
        values.append(_timeline_central_commit_sha(node_metadata, version_metadata))
    return [value for value in _unique_public_values(values) if value]


def _timeline_item_packets(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    support = item.get("support") if isinstance(item.get("support"), dict) else {}
    return _unique_public_values(
        [
            item.get("packet_id"),
            metadata.get("packet_id"),
            metadata.get("source_packet_id"),
            metadata.get("packet_ids"),
            version_metadata.get("linked_packets"),
            support.get("packet_ids"),
        ]
    )


def _timeline_item_evidence(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    support = item.get("support") if isinstance(item.get("support"), dict) else {}
    return _unique_public_values([metadata.get("evidence_refs"), version_metadata.get("evidence_refs"), support.get("evidence_ids")])


def _timeline_item_paths(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    values: list[Any] = [
        metadata.get("path"),
        metadata.get("file_path"),
        metadata.get("normalized_file_path"),
        metadata.get("selected_files"),
        metadata.get("changed_files"),
        version_metadata.get("linked_files"),
        version_metadata.get("file_path"),
    ]
    selected_file_roles = metadata.get("selected_file_roles")
    if isinstance(selected_file_roles, dict):
        values.append(list(selected_file_roles.keys()))
    file_path = _timeline_central_file_path(node_metadata, version_metadata) if node_metadata else ""
    if file_path:
        values.append(file_path)
    return [_normalize_public_path(value) for value in _unique_public_values(values) if value]


def _timeline_item_reasons(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    return _unique_public_values(
        [
            metadata.get("reasons"),
            metadata.get("reasoning_statements"),
            metadata.get("reason"),
            version_metadata.get("statement"),
            version_metadata.get("summary"),
            version_metadata.get("rationale"),
            item.get("reason"),
            item.get("statement"),
        ]
    )


def _timeline_reason_from_item(item: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    for value in (
        item.get("reason"),
        item.get("statement"),
        version_metadata.get("statement"),
        version_metadata.get("summary"),
        version_metadata.get("rationale"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _timeline_message_from_item(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    return re.sub(r"^WP\d+\s+", "", title).strip()


def _timeline_support_label(doc_type: str) -> str:
    labels = {
        "file_impact": "file-impact summary",
        "code_impact": "code-impact summary",
        "reasoning": "accepted reasoning",
        "central_version": "central version",
        "packet": "work packet",
        "symbol_ref": "symbol support",
        "code_region_ref": "code-region support",
    }
    return labels.get(str(doc_type or ""), "")


def _timeline_item_priority(item: dict[str, Any]) -> int:
    doc_type = str(item.get("doc_type") or "")
    if doc_type == "file_impact":
        return 50
    if doc_type == "code_impact":
        return 45
    if doc_type == "reasoning":
        return 42
    if doc_type == "central_version":
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
        return 43 if str(node_metadata.get("atom_kind") or "") in {"decision", "problem"} else 10
    if doc_type == "packet":
        return 35
    return 20


def _timeline_item_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "statement", "reason", "body")
    )


def _normalize_public_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _timeline_central_file_path(metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    canonical_key = str(version_metadata.get("canonical_key") or metadata.get("canonical_key") or "")
    if canonical_key.startswith("file|"):
        parts = canonical_key.split("|", 2)
        return parts[-1] if len(parts) == 3 else ""
    return str(version_metadata.get("file_path") or "")


def _timeline_central_commit_sha(metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    canonical_key = str(version_metadata.get("canonical_key") or metadata.get("canonical_key") or "")
    if canonical_key.startswith("commit|"):
        parts = canonical_key.split("|", 2)
        return parts[-1] if len(parts) == 3 else ""
    return str(version_metadata.get("commit_sha") or "")


def _unique_public_values(values: Iterable[Any]) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return _unique_nonempty([values])
    return _unique_nonempty(values)


def _focused_context_items(*, query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    locator_terms = _answer_code_locator_terms(query)
    if not locator_terms:
        return items
    focused = [item for item in items if _item_matches_terms(item, locator_terms)]
    if not focused:
        return items
    support = _merged_support(focused)
    packet_ids = set(support.get("packet_ids") or [])
    commit_shas = set(support.get("commit_shas") or [])
    out: list[dict[str, Any]] = []
    for item in items:
        item_support = item.get("support") if isinstance(item.get("support"), dict) else {}
        shares_anchor = bool(packet_ids.intersection(item_support.get("packet_ids") or [])) or bool(
            commit_shas.intersection(item_support.get("commit_shas") or [])
        )
        if _item_matches_terms(item, locator_terms) or shares_anchor:
            out.append(item)
    return out or focused


def _answer_code_locator_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or "")):
        lowered = token.lower().replace("\\", "/")
        if "_" in lowered or "::" in lowered or "/" in lowered or "." in lowered:
            terms.add(lowered)
            terms.update(part for part in re.split(r"[^a-zA-Z0-9_]+", lowered) if len(part) > 2)
    return terms


def _item_matches_terms(item: dict[str, Any], terms: set[str]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("title", "statement", "reason")).lower()
    return any(term in text for term in terms)


def _context_bucket(items: list[dict[str, Any]], doc_types: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        doc_type = str(item.get("doc_type") or "")
        if doc_type not in doc_types:
            continue
        if doc_type == "central_version" and not _central_version_context_is_reasoning(item):
            continue
        title = str(item.get("title") or "")
        statement = str(item.get("statement") or "")
        key = (title.lower(), statement.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _central_version_context_is_reasoning(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip().lower()
    if title.startswith(("decision:", "problem:")):
        return True
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    return str(node_metadata.get("atom_kind") or "") in {"decision", "problem"}


def _context_line(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    statement = str(item.get("statement") or "").strip()
    reason = str(item.get("reason") or "").strip()
    parts = []
    if title:
        parts.append(title)
    if statement and statement.lower() != title.lower():
        parts.append(statement)
    if reason and reason.lower() not in {statement.lower(), title.lower()}:
        parts.append(f"reason: {reason}")
    line = " - ".join(parts) if parts else "retrieved support"
    return _clip(line, 520)


def _support_from_version_timeline(version_timeline: dict[str, Any]) -> dict[str, Any]:
    entries = version_timeline.get("entries") if isinstance(version_timeline.get("entries"), list) else []
    return {
        "packet_ids": _unique_public_values(entry.get("packets") for entry in entries if isinstance(entry, dict)),
        "commit_shas": _unique_public_values(entry.get("commit_sha") for entry in entries if isinstance(entry, dict)),
        "evidence_ids": _unique_public_values(entry.get("evidence") for entry in entries if isinstance(entry, dict)),
        "code_node_ids": [],
        "code_nodes": [],
        "neighbor_node_ids": [],
    }


def _merge_public_support(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    keys = ("packet_ids", "commit_shas", "evidence_ids", "code_node_ids", "code_nodes", "neighbor_node_ids")
    return {key: _unique_public_values([left.get(key), right.get(key)]) for key in keys}


def _merged_support(items: list[dict[str, Any]]) -> dict[str, Any]:
    packet_ids: list[Any] = []
    commit_shas: list[Any] = []
    evidence_ids: list[Any] = []
    code_node_ids: list[Any] = []
    code_nodes: list[Any] = []
    neighbor_node_ids: list[Any] = []
    for item in items:
        support = item.get("support") if isinstance(item.get("support"), dict) else {}
        packet_ids.append(support.get("packet_ids"))
        commit_shas.append(support.get("commit_shas"))
        evidence_ids.append(support.get("evidence_ids"))
        code_node_ids.append(support.get("code_node_ids"))
        code_nodes.append(support.get("code_nodes"))
        neighbor_node_ids.append(support.get("neighbor_node_ids"))
    return {
        "packet_ids": _unique_nonempty(packet_ids),
        "commit_shas": _unique_nonempty(commit_shas),
        "evidence_ids": _unique_nonempty(evidence_ids),
        "code_node_ids": _unique_nonempty(code_node_ids),
        "code_nodes": _unique_nonempty(code_nodes)[:12],
        "neighbor_node_ids": _unique_nonempty(neighbor_node_ids),
    }


def _fallback_trace_from_retrieval_doc(*, doc: dict[str, Any], node_id: str, support: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal trace when a curated retrieval doc is not in this graph.

    Repo central retrieval often opens the central Kuzu graph while support docs
    still point at curated session graph ids. In that case graph traversal cannot
    start from the support doc, but the retrieval projection already carries the
    packet/commit/evidence/file provenance that must not be hidden.
    """

    if not any(support.get(key) for key in ("packet_ids", "commit_shas", "evidence_ids", "code_node_ids", "code_nodes")):
        return {}
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    seed = {
        "id": node_id or doc.get("doc_id"),
        "kind": doc.get("node_kind") or doc.get("doc_type") or "RetrievalDocument",
        "role": doc.get("doc_type") or doc.get("node_kind") or "support",
        "label": doc.get("title") or node_id or doc.get("doc_id"),
        "summary": _clip(str(doc.get("body") or ""), 260),
        "packet_id": doc.get("packet_id") or metadata.get("packet_id"),
        "commit_sha": doc.get("commit_sha") or metadata.get("commit_sha"),
        "evidence_id": "",
        "distance": 0,
        "score": 0.0,
    }
    return {
        "seed_node_id": node_id,
        "max_depth": 0,
        "node_count": 1,
        "source": "retrieval_document_metadata",
        "chain": [seed],
        "packets": [{"id": value, "kind": "Packet", "label": value} for value in support.get("packet_ids", [])[:3]],
        "commits": [{"id": value, "kind": "Commit", "label": value} for value in support.get("commit_shas", [])[:4]],
        "evidence": [{"id": value, "kind": "EvidenceRef", "label": value} for value in support.get("evidence_ids", [])[:5]],
        "code_hunks": [],
        "code_nodes": [{"id": value, "kind": "CodeRef", "label": value} for value in support.get("code_node_ids", [])[:8]],
        "symbols": [],
        "paths": [],
        "support": {
            "packet_ids": support.get("packet_ids", []),
            "commit_shas": support.get("commit_shas", []),
            "evidence_ids": support.get("evidence_ids", []),
            "code_node_ids": support.get("code_node_ids", []),
            "code_nodes": support.get("code_nodes", []),
            "neighbor_node_ids": support.get("neighbor_node_ids", []),
        },
    }


def _public_trace_summary(trace: dict[str, Any]) -> str:
    if not trace or not trace.get("node_count"):
        return ""
    chain_parts: list[str] = []
    for item in trace.get("chain") or []:
        role = str(item.get("role") or "").strip()
        kind = str(item.get("kind") or "").strip()
        public_role = _public_trace_role(role or kind)
        if public_role == "accepted reasoning":
            summary = _public_answer_text(str(item.get("summary") or item.get("label") or ""))
            value = f"{public_role}: {summary}" if summary else public_role
        else:
            value = public_role
        if value and value not in chain_parts:
            chain_parts.append(value)
    support_parts: list[str] = []
    support = trace.get("support") if isinstance(trace.get("support"), dict) else {}
    if support.get("commit_shas"):
        support_parts.append("commit-backed")
    if support.get("evidence_ids"):
        support_parts.append("evidence-backed")
    if support.get("code_nodes"):
        support_parts.append("code-backed")
    return " -> ".join([*chain_parts[:4], *support_parts])


def _public_trace_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    labels = {
        "central_version": "active memory version",
        "knowledgeversion": "active memory version",
        "reasoning": "accepted reasoning",
        "reasoningnode": "accepted reasoning",
        "file_impact": "changed file",
        "fileimpactsummary": "changed file",
        "code_impact": "implementation change",
        "codeimpactsummary": "implementation change",
        "commit": "commit support",
        "packet": "session support",
        "evidence": "evidence support",
        "evidenceref": "evidence support",
    }
    return labels.get(normalized, "")


def _public_support_summary(support: dict[str, Any]) -> str:
    parts: list[str] = []
    if support.get("packet_ids"):
        parts.append("session context")
    if support.get("commit_shas"):
        parts.append("commit-backed")
    if support.get("evidence_ids"):
        parts.append("evidence-backed")
    if support.get("code_nodes") or support.get("code_node_ids"):
        parts.append("code-linked")
    return ", ".join(parts)


def _public_answer_title(*, doc: dict[str, Any], graph_node: dict[str, Any], fallback: str) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_type = str(doc.get("doc_type") or "").strip().lower()
    if doc_type == "file_impact":
        path = str(metadata.get("path") or "").strip()
        if path:
            return f"Changes in {path}"
    if doc_type == "code_impact":
        commit_messages = metadata.get("commit_messages") if isinstance(metadata.get("commit_messages"), list) else []
        if commit_messages:
            return _public_answer_text(str(commit_messages[0]))
    return _public_answer_text(str(doc.get("title") or graph_node.get("label") or fallback))


def _public_answer_statement(*, doc: dict[str, Any], graph_node: dict[str, Any], body: str) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_type = str(doc.get("doc_type") or "").strip().lower()
    if doc_type == "packet":
        for line in body.splitlines():
            cleaned = _public_answer_text(line).strip()
            if cleaned and not cleaned.lower().startswith("packet:"):
                return cleaned
    if doc_type == "file_impact":
        path = str(metadata.get("path") or "").strip()
        reasons = metadata.get("reasons") if isinstance(metadata.get("reasons"), list) else []
        reason = _public_answer_text(str(reasons[0])) if reasons else ""
        if path and reason:
            return f"{path} changed because {reason}"
        if path:
            return f"{path} changed in the retrieved work."
    if doc_type == "code_impact":
        reason = _public_answer_text(str(metadata.get("reason") or ""))
        if reason:
            return reason
    if doc_type == "reasoning":
        statement = _public_answer_text(str(metadata.get("statement") or ""))
        if statement:
            return statement
    return _public_answer_text(_body_field(body, "statement") or _best_answer_line(body) or str(graph_node.get("summary") or ""))


def _public_answer_text(text: str) -> str:
    cleaned = re.sub(r"\{[^{}]{0,2000}\}", "", str(text or ""))
    cleaned = re.sub(r"\{.*$", "", cleaned)
    cleaned = re.sub(r"\b(?:FileImpactSummary|CodeImpactSummary|ReasoningNode):\s*", "", cleaned)
    cleaned = re.sub(r"\bImpact summary for\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bWP\d{3,}\b", "work item", cleaned)
    cleaned = re.sub(r"\bE\d{3,}\b", "evidence record", cleaned)
    cleaned = re.sub(r"\bpacket\s+work item\b", "work item", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwork packet\b", "work item", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bevidence\s+evidence record\b", "evidence record", cleaned, flags=re.IGNORECASE)
    return cleaned


def _answer_support(
    *,
    doc: dict[str, Any],
    graph_node: dict[str, Any],
    neighbors: list[dict[str, Any]],
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = graph_node.get("metadata") if isinstance(graph_node.get("metadata"), dict) else {}
    doc_meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    trace_support = trace.get("support") if isinstance(trace, dict) and isinstance(trace.get("support"), dict) else {}
    packet_ids = _unique_nonempty(
        [
            doc.get("packet_id"),
            graph_node.get("packet_id"),
            metadata.get("packet_id"),
            metadata.get("source_packet_id"),
            *(neighbor.get("packet_id") for neighbor in neighbors),
            *(neighbor.get("label") for neighbor in neighbors if str(neighbor.get("kind") or "") == "Packet"),
            trace_support.get("packet_ids"),
        ]
    )
    commit_shas = _unique_nonempty(
        [
            doc.get("commit_sha"),
            graph_node.get("commit_sha"),
            graph_node.get("commit_id"),
            metadata.get("commit_sha"),
            metadata.get("source_commit_sha"),
            *(neighbor.get("commit_sha") for neighbor in neighbors),
            *(neighbor.get("commit_id") for neighbor in neighbors),
            trace_support.get("commit_shas"),
        ]
    )
    evidence_values: list[Any] = [
        graph_node.get("evidence_id"),
        metadata.get("evidence_id"),
        metadata.get("evidence_refs"),
        doc_meta.get("evidence_refs"),
    ]
    evidence_values.extend(neighbor.get("evidence_id") for neighbor in neighbors)
    evidence_values.extend(neighbor.get("id") for neighbor in neighbors if str(neighbor.get("kind") or "") == "EvidenceRef")
    evidence_values.extend(trace_support.get("evidence_ids") or [])
    evidence_ids = _unique_nonempty(evidence_values)
    code_neighbors = [
        neighbor
        for neighbor in neighbors
        if str(neighbor.get("kind") or "") in {"CodeNode", "CodeVersion", "CodeHunk", "Symbol", "SymbolVersion"}
    ]
    code_node_ids = _unique_nonempty(
        [
            *(neighbor.get("id") for neighbor in code_neighbors),
            trace_support.get("code_node_ids"),
        ]
    )
    code_nodes = _unique_nonempty(
        [
            *(neighbor.get("label") or neighbor.get("summary") for neighbor in code_neighbors),
            trace_support.get("code_nodes"),
        ]
    )[:8]
    neighbor_node_ids = _unique_nonempty(
        [
            *(neighbor.get("id") for neighbor in neighbors),
            trace_support.get("neighbor_node_ids"),
        ]
    )
    summary_parts = []
    if packet_ids:
        summary_parts.append("packet " + ", ".join(packet_ids[:3]))
    if commit_shas:
        summary_parts.append("commit " + ", ".join(commit_shas[:3]))
    if evidence_ids:
        summary_parts.append("evidence " + ", ".join(evidence_ids[:3]))
    if code_nodes:
        summary_parts.append("code " + "; ".join(code_nodes[:3]))
    return {
        "packet_ids": packet_ids,
        "commit_shas": commit_shas,
        "evidence_ids": evidence_ids,
        "code_node_ids": code_node_ids,
        "code_nodes": code_nodes,
        "neighbor_node_ids": neighbor_node_ids,
        "summary": " | ".join(summary_parts),
    }


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
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
    return out


def _body_field(body: str, field: str) -> str:
    prefix = f"{field.strip().lower()}:"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[-1].strip()
    return ""


def _best_answer_line(body: str) -> str:
    for prefix in ("statement:", "summary:", "reason:", "symbol:", "file_path:"):
        for line in body.splitlines():
            if line.strip().lower().startswith(prefix):
                return line.split(":", 1)[-1].strip()
    return body.strip().splitlines()[0][:300] if body.strip() else ""


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


def _central_answer_trace_from_retrieval(
    settings: Settings,
    *,
    repo_id: str,
    retrieval: dict[str, Any],
    graph_store: GraphStore | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    view = _active_graph_view_row(settings.db_path, repo_id=repo_id)
    graph_commit_id = str(view.get("graph_commit_id") or "")
    commit = _graph_commit_row(settings.db_path, graph_commit_id=graph_commit_id) if graph_commit_id else {}
    hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
    support_docs = [hit.get("document") for hit in hits if isinstance(hit, dict) and isinstance(hit.get("document"), dict)]
    central_versions = [
        doc
        for doc in support_docs
        if str(doc.get("node_kind") or "") == "KnowledgeVersion" or str(doc.get("doc_type") or "") == "central_version"
    ]
    warning_list = list(warnings)
    if repo_id and not view:
        warning_list.append("active_graph_view_missing")
    elif not graph_commit_id:
        warning_list.append("active_graph_view_head_missing")
    if graph_commit_id and not commit:
        warning_list.append("graph_commit_missing")
    if graph_store is not None and graph_commit_id:
        try:
            central_versions.extend(
                _active_central_versions_for_support(
                    graph_store,
                    repo_id=repo_id,
                    graph_commit_id=graph_commit_id,
                    support_docs=support_docs,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive around optional trace enrichment
            warning_list.append(f"central_version_scan_failed:{type(exc).__name__}")
    return build_central_answer_trace(
        repo_id=repo_id,
        graph_view=view,
        graph_commit=commit,
        central_versions=central_versions,
        support_docs=support_docs,
        warnings=warning_list,
    )


def _active_graph_view_row(db_path: Path, *, repo_id: str) -> dict[str, Any]:
    try:
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM v2_graph_views
                WHERE repo_id = ? AND branch = 'main' AND mode = 'active' AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (repo_id,),
            ).fetchone()
            return dict(row) if row is not None else {}
    except sqlite3.OperationalError:
        return {}


def _active_central_versions_for_support(
    graph_store: GraphStore,
    *,
    repo_id: str,
    graph_commit_id: str,
    support_docs: list[dict[str, Any]],
    limit: int = 50,
) -> list[dict[str, Any]]:
    commit_shas = _support_commit_shas(support_docs)
    file_paths = _support_file_paths(support_docs)
    if not commit_shas and not file_paths:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in graph_store.list_nodes(limit=10000, kinds=["KnowledgeVersion"]):
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        if str(metadata.get("repo_id") or "") != repo_id:
            continue
        # GraphView(main, active) points at the branch head GraphCommit, but
        # active exact versions may have been introduced by earlier commits in
        # the same branch. Treat the central graph as the active view and rely
        # on status/repo/support matching instead of filtering to only the head
        # commit's new versions.
        if graph_commit_id and not str(metadata.get("graph_commit_id") or ""):
            continue
        if str(node.get("status") or metadata.get("status") or "active") != "active":
            continue
        if not _central_version_matches_support(metadata, commit_shas=commit_shas, file_paths=file_paths, repo_id=repo_id):
            continue
        node_id = str(node.get("id") or "")
        if node_id and node_id not in seen:
            seen.add(node_id)
            out.append(node)
        if len(out) >= limit:
            break
    return out


def _support_commit_shas(docs: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for doc in docs:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        for value in (doc.get("commit_sha"), metadata.get("commit_sha")):
            if value:
                values.add(str(value).lower())
        for value in metadata.get("commit_shas") or []:
            if value:
                values.add(str(value).lower())
    return values


def _support_file_paths(docs: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for doc in docs:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        for value in (metadata.get("path"), metadata.get("file_path"), metadata.get("normalized_file_path")):
            if value:
                values.add(str(value))
        selected_files = metadata.get("selected_files")
        if isinstance(selected_files, list):
            values.update(str(value) for value in selected_files if value)
        selected_file_roles = metadata.get("selected_file_roles")
        if isinstance(selected_file_roles, dict):
            values.update(str(value) for value in selected_file_roles if value)
    return values


def _central_version_matches_support(
    metadata: dict[str, Any],
    *,
    commit_shas: set[str],
    file_paths: set[str],
    repo_id: str,
) -> bool:
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    canonical_key = str(version_metadata.get("canonical_key") or "")
    atom_kind = str(metadata.get("atom_kind") or "")
    if atom_kind == "commit":
        commit_sha = canonical_key.removeprefix(f"commit|{repo_id}|").lower()
        return any(_same_commit_sha(commit_sha, candidate) for candidate in commit_shas)
    if atom_kind == "file":
        file_path = canonical_key.removeprefix(f"file|{repo_id}|")
        return file_path in file_paths
    return False


def _same_commit_sha(left: str, right: str) -> bool:
    a = str(left or "").strip().lower()
    b = str(right or "").strip().lower()
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def _graph_commit_row(db_path: Path, *, graph_commit_id: str) -> dict[str, Any]:
    try:
        with connect(db_path) as conn:
            row = conn.execute("SELECT * FROM v2_graph_commits WHERE graph_commit_id = ?", (graph_commit_id,)).fetchone()
            return dict(row) if row is not None else {}
    except sqlite3.OperationalError:
        return {}


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


def _kinds_for_intent(intent: str) -> list[str] | None:
    if intent == "decision_lookup":
        return ["KnowledgeVersion", "ReasoningNode", "DecisionUnit", "Decision", "Fix", "WorkChange", "Prompt", "Response", "ToolResult"]
    if intent == "work_history":
        return ["KnowledgeVersion", "ReasoningNode", "WorkChange", "GitCommit", "Commit", "File", "Decision", "Fix", "ToolResult"]
    if intent == "bug_fix_trace":
        return ["Bug", "Fix", "TestRun", "WorkChange", "ReasoningNode", "GitCommit", "Commit"]
    if intent == "historical_versions":
        return ["KnowledgeVersion", "Decision", "Fix", "WorkChange", "ReasoningNode", "GitCommit", "Commit"]
    return None


def _seed_kinds_for_retrieval(kinds: list[str] | None, *, include_raw: bool) -> list[str] | None:
    if include_raw:
        return kinds
    allowed = set(ANSWER_SEED_KINDS)
    if kinds is None:
        return ANSWER_SEED_KINDS
    filtered = [kind for kind in kinds if kind in allowed]
    return filtered or ANSWER_SEED_KINDS


def _expand_nodes(seed_nodes: list[dict[str, Any]], store: GraphStore) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for node in seed_nodes:
        seen[str(node["id"])] = node
        for neighbor in store.neighbors(str(node["id"]), limit=4):
            seen.setdefault(str(neighbor["id"]), neighbor)
    return list(seen.values())


def _filter_answer_grade_nodes(nodes: list[dict[str, Any]], *, include_raw: bool) -> list[dict[str, Any]]:
    if include_raw:
        return nodes
    filtered: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("kind") in EVIDENCE_ONLY_KINDS:
            continue
        if node.get("kind") in SUPPORT_ONLY_KINDS:
            continue
        if not _is_answer_quality_node(node):
            continue
        filtered.append(node)
    return filtered


def _apply_retrieval_policy(*, query: str, plan: QueryPlan, include_raw: bool) -> QueryPlan:
    raw_allowed = bool(include_raw or _is_explicit_raw_request(query))
    include_raw_final = bool(plan.include_raw and raw_allowed) or bool(include_raw)
    intent = plan.intent
    if intent == "raw_evidence" and not include_raw_final:
        intent = "general"
    return QueryPlan(
        intent=intent,
        entities=plan.entities,
        include_raw=include_raw_final,
        include_historical=plan.include_historical,
    )


def _is_explicit_raw_request(query: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(query or "").lower()).strip()
    raw_phrases = (
        "include raw",
        "show raw",
        "raw evidence",
        "raw payload",
        "raw transcript",
        "raw log",
        "raw logs",
        "raw jsonl",
        "raw record",
        "raw records",
        "raw event",
        "raw events",
        "evidence payload",
        "evidence ref",
        "evidence refs",
        "evidence record",
        "evidence records",
        "original payload",
        "verbatim evidence",
    )
    return any(phrase in lowered for phrase in raw_phrases)


def _sanitize_output_node(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata")
    if not isinstance(metadata, dict):
        return node
    cleaned = dict(node)
    cleaned["metadata"] = _sanitize_metadata(metadata)
    return cleaned


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(metadata)
    for key in ("goal", "latest_decision", "next_step"):
        if key in cleaned:
            cleaned[key] = _scalar_metadata_value(cleaned[key])
    for key in ("changed_files", "tests", "blockers", "evidence_ids"):
        if key in cleaned:
            cleaned[key] = _list_metadata_value(cleaned[key])
    return cleaned


def _scalar_metadata_value(value: Any) -> str:
    parsed = _parse_literal_list(value)
    if parsed is not None:
        value = parsed
    if isinstance(value, list):
        rows = [_scalar_metadata_value(item) for item in value]
        return " ".join(row for row in rows if row)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


def _list_metadata_value(value: Any) -> list[str]:
    parsed = _parse_literal_list(value)
    if parsed is not None:
        value = parsed
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _scalar_metadata_value(item)
        if text:
            rows.append(text)
    return rows


def _parse_literal_list(value: Any) -> list[Any] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _is_answer_quality_node(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or "")
    if kind in {"WorkChange", "Decision", "Fix", "Bug", "Blocker", "TestRun"}:
        return _is_clean_answer_summary(str(node.get("summary") or ""), node.get("metadata"))
    return True


def _rank_nodes(
    query: str,
    nodes: list[dict[str, Any]],
    *,
    include_historical: bool,
    require_lexical: bool = False,
) -> list[dict[str, Any]]:
    terms = _retrieval_terms(query)
    query_term_set = set(terms)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        if not include_historical and node.get("status") in {"superseded", "abandoned"}:
            continue
        text = f"{node.get('kind')} {node.get('label')} {node.get('summary')} {json.dumps(node.get('metadata', {}), sort_keys=True)}".lower()
        node_terms = set(_retrieval_terms(text))
        lexical = float(len(query_term_set & node_terms))
        substring = sum(0.25 for term in query_term_set - node_terms if term in text)
        lexical += substring
        graph_score = float(node.get("graph_score") or 0.0)
        if terms and lexical <= 0 and graph_score <= 0:
            continue
        if require_lexical and terms and lexical <= 0:
            continue
        status = str(node.get("status") or "")
        if status == "committed":
            status_bonus = 2.0
        elif status == "active":
            status_bonus = 1.0
        elif status == "draft":
            status_bonus = 0.25
        else:
            status_bonus = 0.0
        evidence_bonus = 0.5 if node.get("evidence_id") else 0.0
        ranked.append((lexical + status_bonus + evidence_bonus + graph_score, node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{**node, "score": round(score, 6)} for score, node in ranked]


def _retrieval_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_.-]+", str(text or "").lower()):
        if len(token) <= 2:
            continue
        if token in RETRIEVAL_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        terms.append(token)
    return terms


def _trim_weak_tail_matches(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not nodes:
        return nodes
    top_score = float(nodes[0].get("score") or 0.0)
    if top_score < 4.0:
        return nodes
    floor = max(2.0, top_score - 3.0)
    trimmed = [node for node in nodes if float(node.get("score") or 0.0) >= floor]
    return trimmed or nodes[:1]


def _is_clean_answer_summary(summary: str, metadata: object = None) -> bool:
    text = summary.strip()
    lowered = text.lower()
    generic_summaries = {
        "update files in the session",
        "git commit operation executed",
        "git commit operation executed.",
    }
    if lowered in generic_summaries:
        return False
    if len(text) < 16:
        return False
    if re.match(r"^(from\s+[\w.]+\s+import\b|import\s+[\w.]+\b|class\s+\w+\b|def\s+\w+\b)", lowered):
        return False
    if re.search(r"\|\s*(from\s+[\w.]+\s+import\b|import\s+[\w.]+\b|class\s+\w+\b|def\s+\w+\b)", lowered):
        return False
    noisy_prefixes = (
        '"continue":',
        "{",
        "[",
        "from __future__",
        "import ",
        "class ",
        "def ",
        "raise ",
        "assert ",
        "return ",
        "all checks passed!",
    )
    noisy_terms = (
        "manualsmoke",
        "captureonly",
        "hook_event_name",
        "status_porcelain",
        "after_preview",
        "raw_",
        "traceback",
        "content-length",
    )
    if lowered.startswith(noisy_prefixes):
        return False
    if any(term in lowered for term in noisy_terms):
        return False
    if text.count(" | ") >= 6 and (
        _code_token_ratio(text) > 0.05
        or "return " in lowered
        or "_write_" in lowered
        or "_read_" in lowered
    ):
        return False
    if len(text) > 600 and text.count(" | ") >= 6:
        return False
    if len(text) > 600 and _code_token_ratio(text) > 0.08:
        return False
    if len(text) > 900:
        return False
    if len(text) > 240 and _punctuation_ratio(text) > 0.18:
        return False
    if len(text) > 240 and _code_token_ratio(text) > 0.18:
        return False
    meta = metadata if isinstance(metadata, dict) else {}
    if meta.get("changed_files"):
        return True
    return True


def _punctuation_ratio(text: str) -> float:
    if not text:
        return 0.0
    punct = sum(1 for ch in text if ch in "{}[]()\\\"=:,")
    return punct / max(1, len(text))


def _code_token_ratio(text: str) -> float:
    tokens = text.split()
    if not tokens:
        return 0.0
    code_like = sum(1 for token in tokens if any(mark in token for mark in ("::", "=>", "()", "=", "{", "}", ";")))
    return code_like / len(tokens)


def _load_evidence_records(
    roots: list[Path],
    *,
    session_id: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.jsonl")):
            for _next_offset, record in _read_jsonl_from(path, 0):
                if session_id and str(record.get("session_id") or "") != session_id:
                    continue
                records.append(record)
    records.sort(key=lambda record: str(record.get("created_at") or ""))
    return records[-max(1, int(limit)) :]


def build_session_detail_fallback(
    settings: Settings,
    *,
    session_id: str,
    limit: int = 120,
    error: Exception | None = None,
) -> dict[str, Any]:
    """Return selected-session detail without opening Kuzu.

    This is the dashboard fallback path when the graph file is temporarily
    unavailable. It still shows immutable raw production artifacts so the operator can
    inspect the session while graph reads recover.
    """

    safe_session_id = str(session_id or "").strip()
    if not safe_session_id:
        raise ValueError("session_id is required")
    safe_limit = max(1, min(500, int(limit)))
    records, evidence_source = _load_session_evidence_records(settings, session_id=safe_session_id, limit=safe_limit)
    pending = _session_pending_summary(settings, session_id=safe_session_id)
    graph_warning = "graph_temporarily_unavailable" if error is not None else "graph_not_loaded_for_fast_session_detail"
    warning = {
        "ok": False,
        "error": str(error or graph_warning),
        "error_type": type(error).__name__ if error is not None else "GraphUnavailable",
        "warning": graph_warning,
    }
    return {
        "ok": True,
        "degraded": error is not None,
        "mode": "artifact_only",
        "session_id": safe_session_id,
        "timeline": [_timeline_row(record) for record in records],
        "windows": _reconstruct_clean_windows(records, []),
        "current_context": {"ok": True, "nodes": [], "source": "not_loaded_graph_unavailable"},
        "merge_status": warning,
        "merge_preview": warning,
        "pending": {"count": pending.get("count", 0), "cursor_path": pending.get("cursor_path"), "source": pending.get("source")},
        "evidence_source": evidence_source,
        "graph": {"nodes": [], "edges": [], "warning": graph_warning},
        "central_graph": {"ok": False, "nodes": [], "edges": [], "warnings": [graph_warning], "status": warning},
    }


def _load_session_evidence_records(settings: Settings, *, session_id: str, limit: int = 500) -> tuple[list[dict[str, Any]], str]:
    artifact_records = _load_production_session_raw_evidence_artifact(settings, session_id=session_id, limit=limit)
    if artifact_records is not None:
        return artifact_records, "production_session_raw_evidence_artifact"
    return _load_evidence_records(_evidence_roots(settings), session_id=session_id, limit=limit), "raw_evidence_scan"


def _load_production_session_raw_evidence_artifact(settings: Settings, *, session_id: str, limit: int = 500) -> list[dict[str, Any]] | None:
    job_store = ProductionSessionJobStore(settings)
    try:
        job = job_store.get_job_by_session(session_id=session_id)
        if not job:
            return None
        stage = job_store.stage_row(job_id=str(job.get("job_id") or ""), stage="evidence_view")
        if not stage:
            return None
    finally:
        job_store.close()
    view_path = Path(str(stage.get("output_artifact") or ""))
    if not view_path.exists():
        return None
    try:
        view = json.loads(view_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_path = Path(str(view.get("input_raw") or ""))
    if not raw_path.exists() or raw_path.is_dir():
        return None
    records = [record for _next_offset, record in _read_jsonl_from(raw_path, 0)]
    records.sort(key=lambda record: str(record.get("created_at") or ""))
    return records[-max(1, int(limit)) :]


def _session_pending_summary(settings: Settings, *, session_id: str) -> dict[str, Any]:
    job_store = ProductionSessionJobStore(settings)
    try:
        job = job_store.get_job_by_session(session_id=session_id)
    finally:
        job_store.close()
    if job:
        return {
            "ok": True,
            "count": 0,
            "pending": [],
            "cursor_path": "",
            "source": "production_job_state",
            "job_status": job.get("status"),
            "current_stage": job.get("current_stage"),
        }
    return {
        "ok": True,
        "count": 0,
        "pending": [],
        "cursor_path": "",
        "source": "not_loaded_no_production_job",
    }


def _timeline_row(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    text = (
        str(payload.get("prompt") or "")
        or str(payload.get("tool_response") or "")
        or str(payload.get("last_assistant_message") or "")
        or command
    )
    return {
        "id": record.get("id"),
        "created_at": record.get("created_at"),
        "event_name": record.get("event_name"),
        "source_app": record.get("source_app"),
        "tool": payload.get("tool") or payload.get("tool_name") or "",
        "command": _clip(command, 260),
        "summary": _clip(text, 420),
        "payload_keys": sorted(payload.keys()),
        "raw_ref": {
            "path": record.get("path"),
            "offset": record.get("offset"),
            "hash": record.get("hash"),
        },
    }


def _reconstruct_clean_windows(records: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending_by_session: dict[str, list[dict[str, Any]]] = {}
    last_active_session_id = ""
    windows: list[dict[str, Any]] = []
    for record in records:
        current_session = record_session_id(record)
        if is_session_start(record):
            if last_active_session_id and last_active_session_id != current_session:
                pending = pending_by_session.get(last_active_session_id, [])
                if pending:
                    decision = session_boundary_trigger(last_active_session_id, current_session)
                    windows.append(_window_row(len(windows) + 1, pending, decision, nodes))
                    pending_by_session[last_active_session_id] = []
            last_active_session_id = current_session
        elif not last_active_session_id:
            last_active_session_id = current_session
        pending_by_session.setdefault(current_session, []).append(record)

    for pending_records in pending_by_session.values():
        if not pending_records:
            continue
        preview_trigger = TriggerDecision(False, "pending", "pending raw evidence window")
        windows.append(
            {
                "index": len(windows) + 1,
                "status": "pending",
                "trigger": preview_trigger.as_dict(),
                "evidence_ids": [str(record.get("id") or "") for record in pending_records if record.get("id")],
                "cleaned_evidence": clean_evidence_window(pending_records, preview_trigger),
                "graph_nodes": [],
                "graph_edges": [],
            }
        )
    return windows


def _window_row(
    index: int,
    records: list[dict[str, Any]],
    trigger: TriggerDecision,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = [str(record.get("id") or "") for record in records if record.get("id")]
    graph_nodes = _nodes_for_evidence(nodes, evidence_ids)
    return {
        "index": index,
        "status": "processed" if graph_nodes else "captured",
        "trigger": trigger.as_dict(),
        "evidence_ids": evidence_ids,
        "cleaned_evidence": clean_evidence_window(records, trigger),
        "graph_nodes": graph_nodes,
    }


def _nodes_for_evidence(nodes: list[dict[str, Any]], evidence_ids: list[str]) -> list[dict[str, Any]]:
    evidence_set = set(evidence_ids)
    matched: list[dict[str, Any]] = []
    for node in nodes:
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        node_evidence = {str(node.get("evidence_id") or "")}
        meta_evidence = metadata.get("evidence_ids")
        if isinstance(meta_evidence, list):
            node_evidence.update(str(item) for item in meta_evidence)
        if evidence_set.intersection(node_evidence):
            matched.append(node)
    return matched[:25]


def _matches_version_flow_filter(node: dict[str, Any], *, commit: str, session_id: str) -> bool:
    if session_id and str(node.get("session_id") or "") != session_id:
        return False
    if commit and commit.upper() != "HEAD" and not _matches_commit(node, commit):
        return False
    return True


def _matches_commit(node: dict[str, Any], commit: str) -> bool:
    needle = str(commit or "").strip().lower()
    if not needle:
        return True
    values = [
        str(node.get("id") or ""),
        str(node.get("label") or ""),
        str(node.get("commit_id") or ""),
    ]
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    commit_meta = meta.get("commit") if isinstance(meta.get("commit"), dict) else {}
    values.append(str(commit_meta.get("commit") or ""))
    return any(value.lower().startswith(needle) or needle in value.lower() for value in values if value)


def _is_central_graph_seed(node: dict[str, Any]) -> bool:
    return str(node.get("scope") or "") == "central" or str(node.get("status") or "") in {"committed", "active"}


def _is_isolated_graph_seed(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or "")
    status = str(node.get("status") or "")
    if kind not in ISOLATED_GRAPH_VISUAL_KINDS:
        return False
    if status in ISOLATED_GRAPH_VISUAL_STATUSES:
        return True
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(metadata.get("stage") or "").startswith("stage")


def _isolated_graph_seed_pool(
    store: GraphStore,
    nodes: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    priority = {
        "ReasoningNode": 0,
        "Decision": 1,
        "Problem": 1,
        "Cause": 1,
        "Fix": 1,
        "Constraint": 1,
        "Commit": 2,
        "GitCommit": 2,
        "Packet": 3,
        "CodeNode": 4,
        "Symbol": 5,
        "CodeVersion": 6,
        "CodeHunk": 7,
        "EvidenceRef": 8,
    }
    rows = list(nodes)
    per_kind_limit = limit if limit > 500 else max(20, min(160, limit))
    for kind in priority:
        rows.extend(store.list_nodes(kinds=[kind], limit=per_kind_limit))
    unique: dict[str, dict[str, Any]] = {}
    for node in rows:
        node_id = str(node.get("id") or "")
        if node_id and node_id not in unique:
            unique[node_id] = node
    return sorted(
        unique.values(),
        key=lambda node: (
            priority.get(str(node.get("kind") or ""), 50),
            str(node.get("commit_id") or ""),
            str(node.get("id") or ""),
        ),
    )


def _build_version_flow(
    *,
    commit_node: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    commit_node_id = str(commit_node.get("id") or "")
    commit_id = str(commit_node.get("commit_id") or "")
    related_edges = [
        edge
        for edge in edges
        if edge.get("kind") in VERSION_FLOW_EDGE_KINDS
        and _edge_mentions_commit(edge, commit_node_id=commit_node_id, commit_id=commit_id)
    ]
    work_ids = {
        str(edge.get("source_id") or "")
        for edge in related_edges
        if edge.get("kind") == "COMMITTED_AS" and str(edge.get("target_id") or "") == commit_node_id
    }
    work_ids.update(
        str(node.get("id") or "")
        for node in nodes
        if str(node.get("commit_id") or "") == commit_id and str(node.get("kind") or "") in ANSWER_SEED_KINDS
    )
    work_ids.discard(commit_node_id)

    flow_edge_ids = {str(edge.get("id") or "") for edge in related_edges}
    frontier = set(work_ids) | {commit_node_id}
    for edge in edges:
        kind = str(edge.get("kind") or "")
        source = str(edge.get("source_id") or "")
        target = str(edge.get("target_id") or "")
        if kind not in VERSION_FLOW_EDGE_KINDS:
            continue
        if source in frontier or target in frontier:
            flow_edge_ids.add(str(edge.get("id") or ""))
            if kind in {"MODIFIES", "VALIDATED_BY", *VERSION_RELATION_EDGE_KINDS, "MERGED_INTO"}:
                frontier.update([source, target])

    flow_edges = [edge for edge in edges if str(edge.get("id") or "") in flow_edge_ids]
    flow_node_ids = {commit_node_id}
    for edge in flow_edges:
        flow_node_ids.add(str(edge.get("source_id") or ""))
        flow_node_ids.add(str(edge.get("target_id") or ""))
    flow_node_ids.update(work_ids)
    flow_nodes = [node_by_id[node_id] for node_id in flow_node_ids if node_id in node_by_id]
    work_nodes = [node_by_id[node_id] for node_id in work_ids if node_id in node_by_id]
    files = _flow_nodes_for_edges(flow_edges, node_by_id, kind="MODIFIES", endpoint="target")
    tests = _flow_nodes_for_edges(flow_edges, node_by_id, kind="VALIDATED_BY", endpoint="source")
    evidence_ids = sorted(
        {
            str(value)
            for value in [
                commit_node.get("evidence_id"),
                *(node.get("evidence_id") for node in work_nodes),
                *(edge.get("evidence_id") for edge in flow_edges),
            ]
            if value
        }
    )
    version_edges = [
        edge
        for edge in flow_edges
        if _is_version_relation_edge(edge) and _has_durable_relation_endpoints(edge, node_by_id)
    ]
    return {
        "commit_node": commit_node,
        "commit_id": commit_id,
        "summary": _version_flow_summary(commit_node, work_nodes, files),
        "counts": {
            "work_nodes": len(work_nodes),
            "files": len(files),
            "tests": len(tests),
            "version_edges": len(version_edges),
            "evidence_refs": len(evidence_ids),
        },
        "work_nodes": work_nodes,
        "files": files,
        "tests": tests,
        "evidence_ids": evidence_ids,
        "version_edges": version_edges,
        "edges": flow_edges,
        "nodes": flow_nodes,
    }


def _build_central_version_flows(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    commit: str,
    session_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    graph_commits = [
        node
        for node in nodes
        if str(node.get("kind") or "") == "GraphCommit"
        and str(node.get("status") or "") == "applied"
        and (not session_id or str(node.get("session_id") or _node_metadata(node).get("session_id") or "") == session_id)
    ]
    graph_commits.sort(key=lambda node: str(node.get("created_at") or node.get("updated_at") or ""), reverse=True)
    flows: list[dict[str, Any]] = []
    for graph_commit in graph_commits:
        graph_commit_id = str(graph_commit.get("id") or "")
        versions = [
            node
            for node in nodes
            if str(node.get("kind") or "") == "KnowledgeVersion"
            and str(_node_metadata(node).get("graph_commit_id") or "") == graph_commit_id
        ]
        if commit and commit.upper() != "HEAD" and not any(_central_version_matches_commit(node, commit) for node in versions):
            continue
        if not versions:
            continue
        version_ids = {str(node.get("id") or "") for node in versions}
        version_edges = [
            edge
            for edge in edges
            if str(edge.get("kind") or "") == "VERSION_OF" and str(edge.get("source_id") or "") in version_ids
        ]
        atoms = [
            node_by_id[str(edge.get("target_id") or "")]
            for edge in version_edges
            if str(edge.get("target_id") or "") in node_by_id
        ]
        commit_versions = [node for node in versions if _central_version_atom_kind(node) == "commit"]
        file_versions = [node for node in versions if _central_version_atom_kind(node) == "file"]
        commit_ids = sorted({_central_version_commit_id(node) for node in commit_versions if _central_version_commit_id(node)})
        file_paths = sorted({_central_version_file_path(node) for node in file_versions if _central_version_file_path(node)})
        flow = {
            "flow_type": "central_version",
            "graph_commit_id": graph_commit_id,
            "parent_graph_commit_id": str(_node_metadata(graph_commit).get("parent_graph_commit_id") or ""),
            "commit_id": commit_ids[0] if len(commit_ids) == 1 else "",
            "commit_ids": commit_ids,
            "session_id": str(graph_commit.get("session_id") or _node_metadata(graph_commit).get("session_id") or ""),
            "job_id": str(_node_metadata(graph_commit).get("job_id") or ""),
            "plan_id": str(_node_metadata(graph_commit).get("merge_plan_id") or ""),
            "versions": versions,
            "commit_versions": commit_versions,
            "file_versions": file_versions,
            "files": file_paths,
            "nodes": [graph_commit, *versions, *atoms],
            "edges": version_edges,
            "evidence_ids": [],
            "counts": {
                "work_nodes": len(versions),
                "commit_versions": len(commit_versions),
                "file_versions": len(file_versions),
                "version_edges": len(version_edges),
            },
            "summary": _central_version_flow_summary(graph_commit, commit_ids, file_paths),
        }
        flows.append(flow)
        if len(flows) >= limit:
            break
    return flows


def _node_metadata(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _central_version_atom_kind(node: dict[str, Any]) -> str:
    return str(_node_metadata(node).get("atom_kind") or "")


def _central_version_matches_commit(node: dict[str, Any], commit: str) -> bool:
    needle = str(commit or "").strip().lower()
    if not needle:
        return True
    metadata = _node_metadata(node)
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    source_node_ids = metadata.get("source_node_ids") if isinstance(metadata.get("source_node_ids"), list) else []
    values = [
        str(node.get("id") or ""),
        str(node.get("label") or ""),
        str(node.get("summary") or ""),
        str(version_metadata.get("canonical_key") or ""),
        " ".join(str(value) for value in source_node_ids if value),
    ]
    return any(needle in value.lower() for value in values if value)


def _central_version_commit_id(node: dict[str, Any]) -> str:
    metadata = _node_metadata(node)
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    canonical_key = str(version_metadata.get("canonical_key") or node.get("label") or "")
    parts = canonical_key.split("|")
    if len(parts) >= 3 and parts[0] == "commit":
        return parts[-1]
    source_node_ids = metadata.get("source_node_ids") if isinstance(metadata.get("source_node_ids"), list) else []
    for source_id in source_node_ids:
        if str(source_id).startswith("commit:"):
            return str(source_id).split(":", 1)[1]
    return ""


def _central_version_file_path(node: dict[str, Any]) -> str:
    metadata = _node_metadata(node)
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    canonical_key = str(version_metadata.get("canonical_key") or node.get("label") or "")
    parts = canonical_key.split("|")
    if len(parts) >= 4 and parts[0] == "file":
        return "|".join(parts[2:-1])
    if len(parts) >= 3 and parts[0] == "file":
        return parts[-1]
    return ""


def _central_version_flow_summary(graph_commit: dict[str, Any], commit_ids: list[str], file_paths: list[str]) -> str:
    commit_text = ", ".join(commit_id[:12] for commit_id in commit_ids[:4]) or "no commit versions"
    file_text = ", ".join(file_paths[:5]) or "no file versions"
    suffix = "" if len(file_paths) <= 5 else f", +{len(file_paths) - 5} more files"
    return _clip(f"{graph_commit.get('id')} applied commit/file versions: commits {commit_text}; files {file_text}{suffix}", 520)


def _edge_mentions_commit(edge: dict[str, Any], *, commit_node_id: str, commit_id: str) -> bool:
    if str(edge.get("source_id") or "") == commit_node_id or str(edge.get("target_id") or "") == commit_node_id:
        return True
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    edge_commit = str(metadata.get("commit_id") or metadata.get("commit") or "")
    return bool(commit_id and edge_commit == commit_id)


def _flow_nodes_for_edges(
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    *,
    kind: str,
    endpoint: str,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for edge in edges:
        if str(edge.get("kind") or "") != kind:
            continue
        node_id = str(edge.get("target_id" if endpoint == "target" else "source_id") or "")
        if node_id in seen or node_id not in node_by_id:
            continue
        seen.add(node_id)
        rows.append(node_by_id[node_id])
    return rows


def _is_version_relation_edge(edge: dict[str, Any]) -> bool:
    kind = str(edge.get("kind") or "")
    if kind in {"DUPLICATE_OF", "SUPERSEDES", "CONTRADICTS"}:
        return True
    if kind != "REFINES":
        return False
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    return any(key in metadata for key in ("reason", "source", "score", "commit_id"))


def _has_durable_relation_endpoints(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> bool:
    source = node_by_id.get(str(edge.get("source_id") or ""))
    target = node_by_id.get(str(edge.get("target_id") or ""))
    if not source or not target:
        return False
    return all(
        str(node.get("status") or "") in {"committed", "active", "superseded"}
        or str(node.get("scope") or "") == "central"
        for node in (source, target)
    )


def _version_flow_summary(commit_node: dict[str, Any], work_nodes: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    subject = str(commit_node.get("summary") or commit_node.get("label") or "Commit")
    work = "; ".join(_clip(node.get("summary") or node.get("label"), 90) for node in work_nodes[:3] if node)
    file_text = ", ".join(str(node.get("label") or "") for node in files[:5] if node.get("label"))
    parts = [subject]
    if work:
        parts.append(f"promoted: {work}")
    if file_text:
        parts.append(f"files: {file_text}")
    return _clip(" | ".join(parts), 520)


def _version_flow_warnings(flows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not flows:
        warnings.append("no_committed_version_flows_found")
        return warnings
    if not any(flow.get("counts", {}).get("work_nodes") for flow in flows):
        warnings.append("version_flows_have_no_promoted_work_nodes")
    if not any(flow.get("counts", {}).get("version_edges") for flow in flows):
        warnings.append("version_flows_have_no_refine_supersede_duplicate_edges")
    return warnings


def _clip(value: object, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _evidence_roots(settings: Settings) -> list[Path]:
    roots: list[Path] = [settings.evidence_dir]
    workspace = os.getenv("AMO_WORKSPACE_CWD") or os.getcwd()
    try:
        spool = Path(workspace).expanduser().resolve() / ".amo-spool" / "evidence"
        if spool != settings.evidence_dir and spool.exists():
            roots.append(spool)
    except OSError:
        pass
    return roots


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

