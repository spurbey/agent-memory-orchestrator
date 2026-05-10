from __future__ import annotations

import ast
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from ..adapters import normalize_adapter_event
from ..config import Settings
from ..evidence.drain import EvidenceDrain
from ..evidence.drain import _read_jsonl_from
from ..evidence.raw_store import RawEvidenceRef, RawEvidenceStore
from ..evidence.triggers import TriggerDecision, detect_trigger
from ..evidence.window import clean_evidence_window
from ..llm.qwen import DeterministicPlanner, OllamaQwenClient, QueryPlan, QwenPlanner, QwenUnavailable
from ..versioning import LocalGitBackend, VersionBackend
from ..work_ledger import WorkLedger
from .cache import GraphSearchCache
from .consolidation import DeterministicGraphConsolidator
from .merge import CommitMergeEngine, QwenMergeClassifier
from .session import QwenGraphExtractor, SessionGraphBuilder
from .store import GraphEdge, GraphNode, GraphStore, KuzuGraphStore


HOOK_CONTEXT_EVENTS = {"session_start"}
CAPTURE_ONLY_EVENTS = {"user_prompt_submit", "prompt", "post_tool_use", "tool_result", "stop", "session_stop"}
EVIDENCE_ONLY_KINDS = {"RawEvidenceRef", "Prompt", "ToolUse", "ToolResult", "Turn", "Session", "App", "Repo", "Branch"}
SUPPORT_ONLY_KINDS = {"File", "Symbol", "Topic", "CleanedEvidenceWindow", "GraphDelta"}
ANSWER_SEED_KINDS = ["ContextSnapshot", "WorkChange", "Decision", "Fix", "Bug", "Blocker", "TestRun", "GitCommit"]


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
    ) -> None:
        self.settings = settings
        self.store = store or KuzuGraphStore(settings.graph_path)
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
        self.search_cache = GraphSearchCache(settings.home / ".cache" / "graph_nodes_bm25.json")
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
        cache_nodes = [] if raw_requested else self.search_cache.search(query, limit=search_limit, kinds=kinds)
        seed_nodes = _merge_seed_nodes(self.store.search_nodes(query, limit=search_limit, kinds=kinds), cache_nodes)
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
        nodes = [
            _sanitize_output_node(node)
            for node in self.store.list_nodes(kinds=["ContextSnapshot"], session_id=session_id, limit=max(safe_limit * 5, 25))
            if _is_clean_context_snapshot(node)
        ][:safe_limit]
        return {
            "ok": True,
            "session_id": session_id,
            "count": len(nodes),
            "nodes": nodes,
            "context": _context_from_snapshots(nodes),
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
        result = drain.drain(limit=max(1, int(limit)), session_id=session_id, max_windows=max_windows)
        finalizations: list[dict[str, Any]] = []
        for row in result.get("triggered", []):
            if not isinstance(row, dict):
                continue
            trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
            if not _is_finalize_boundary(trigger):
                continue
            latest_event = row.get("latest_event") if isinstance(row.get("latest_event"), dict) else {}
            git = latest_event.get("git") if isinstance(latest_event.get("git"), dict) else {}
            commit = str(git.get("head") or "")
            current_session = str(row.get("session_id") or session_id or "")
            if not current_session:
                continue
            commit = commit or f"finalize:{current_session}"
            finalizations.append(
                self.finalize_session(
                    session_id=current_session,
                    commit=commit,
                    apply=True,
                    cwd=str(git.get("repo_root") or "") or None,
                )
            )
        if finalizations:
            result["finalizations"] = finalizations
        return result

    def pending_evidence(self, *, session_id: str = "") -> dict[str, Any]:
        drain = self._new_drain()
        return drain.pending(session_id=session_id)

    def finalize_session(
        self,
        *,
        session_id: str,
        commit: str = "HEAD",
        apply: bool = False,
        limit: int = 500,
        cwd: str | Path | None = None,
    ) -> dict[str, Any]:
        classifier = QwenMergeClassifier(self.settings) if self.settings.qwen_runtime == "ollama" else None
        engine = CommitMergeEngine(self.settings, self.store, self.version_backend, classifier=classifier)
        result = engine.finalize_session(session_id=session_id, commit=commit, apply=apply, limit=limit, cwd=cwd)
        if apply and result.get("ok"):
            result["consolidation"] = self.consolidate_graph(limit=limit, apply=True)
            result["cache"] = self.rebuild_graph_cache(limit=max(limit, 5000))
        return result

    def rebuild_central_from_evidence(
        self,
        *,
        apply: bool = False,
        backup_current: bool = True,
        limit: int = 100000,
        max_windows: int | None = None,
    ) -> dict[str, Any]:
        roots = _evidence_roots(self.settings)
        evidence_files = [path for root in roots if root.exists() for path in sorted(root.glob("*.jsonl"))]
        evidence_count = sum(len(_read_jsonl_from(path, 0)) for path in evidence_files)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        current_path = self.settings.graph_path
        rebuild_path = current_path.parent / f"{current_path.name}.rebuild-{timestamp}"
        backup_path = current_path.parent / f"{current_path.name}.backup-{timestamp}"
        plan: dict[str, Any] = {
            "ok": True,
            "apply": bool(apply),
            "from_evidence": True,
            "evidence_roots": [str(root) for root in roots],
            "evidence_files": [str(path) for path in evidence_files],
            "evidence_count": evidence_count,
            "current_graph_path": str(current_path),
            "rebuild_graph_path": str(rebuild_path),
            "backup_graph_path": str(backup_path) if backup_current else "",
            "limit": max(1, int(limit)),
            "max_windows": max_windows or self.settings.drain_max_windows_per_run,
        }
        if not apply:
            return plan

        if hasattr(self.store, "nodes") and hasattr(self.store, "edges"):
            self.store.nodes.clear()  # type: ignore[attr-defined]
            self.store.edges.clear()  # type: ignore[attr-defined]
            rebuild_result = self._drain_fresh_graph(
                self.store,
                cursor_path=self.settings.home / ".state" / f"rebuild-cursors-{timestamp}.json",
                limit=limit,
                max_windows=max_windows,
            )
            plan.update(rebuild_result)
            plan["consolidation"] = self.consolidate_graph(limit=5000, apply=True)
            plan["cache"] = self.rebuild_graph_cache(limit=20000)
            return plan

        self.store.close()
        rebuild_store = KuzuGraphStore(rebuild_path)
        rebuild_store.init_schema()
        try:
            rebuild_result = self._drain_fresh_graph(
                rebuild_store,
                cursor_path=self.settings.home / ".state" / f"rebuild-cursors-{timestamp}.json",
                limit=limit,
                max_windows=max_windows,
            )
            consolidator = DeterministicGraphConsolidator(rebuild_store, project_id=self.settings.project_id)
            consolidation = consolidator.consolidate(limit=5000, apply=True).as_dict()
            smoke = _rebuild_smoke(rebuild_store)
        finally:
            rebuild_store.close()
        if not smoke.get("ok"):
            plan.update({"ok": False, "error": "rebuild_smoke_failed", "smoke": smoke})
            return plan

        current_path.parent.mkdir(parents=True, exist_ok=True)
        if current_path.exists() and backup_current:
            _move_graph_path(current_path, backup_path)
        elif current_path.exists():
            _remove_graph_path(current_path)
        _move_graph_path(rebuild_path, current_path)
        self.store = KuzuGraphStore(current_path)
        self.store.init_schema()
        plan.update(rebuild_result)
        plan["consolidation"] = consolidation
        plan["smoke"] = smoke
        plan["cache"] = self.rebuild_graph_cache(limit=20000)
        plan["swapped"] = True
        return plan

    def _drain_fresh_graph(
        self,
        store: GraphStore,
        *,
        cursor_path: Path,
        limit: int,
        max_windows: int | None,
    ) -> dict[str, Any]:
        classifier = QwenMergeClassifier(self.settings) if self.settings.qwen_runtime == "ollama" else None
        extractor = QwenGraphExtractor(self.settings)
        builder = SessionGraphBuilder(self.settings, store, self.version_backend, extractor=extractor)
        drain = EvidenceDrain(
            self.settings,
            store,
            self.version_backend,
            cursor_path=cursor_path,
            evidence_roots=_evidence_roots(self.settings),
            builder=builder,
        )
        total: dict[str, Any] = {
            "records_seen": 0,
            "records_ingested": 0,
            "records_skipped": 0,
            "windows_processed": 0,
            "triggered": [],
            "finalizations": [],
            "stopped_reason": "",
        }
        remaining = max(1, int(limit))
        while remaining > 0:
            chunk = drain.drain(limit=remaining, max_windows=max_windows or 1000)
            for key in ("records_seen", "records_ingested", "records_skipped", "windows_processed"):
                total[key] += int(chunk.get(key) or 0)
            total["triggered"].extend(chunk.get("triggered") or [])
            for row in chunk.get("triggered") or []:
                trigger = row.get("trigger") if isinstance(row, dict) and isinstance(row.get("trigger"), dict) else {}
                latest_event = row.get("latest_event") if isinstance(row, dict) and isinstance(row.get("latest_event"), dict) else {}
                git = latest_event.get("git") if isinstance(latest_event.get("git"), dict) else {}
                if not _is_finalize_boundary(trigger):
                    continue
                commit = str(git.get("head") or "") or f"finalize:{row.get('session_id') or 'session'}"
                engine = CommitMergeEngine(self.settings, store, self.version_backend, classifier=classifier)
                total["finalizations"].append(
                    engine.finalize_session(
                        session_id=str(row.get("session_id") or ""),
                        commit=commit,
                        cwd=str(git.get("repo_root") or "") or None,
                        apply=True,
                    )
                )
            remaining -= int(chunk.get("records_ingested") or 0)
            total["stopped_reason"] = chunk.get("stopped_reason") or ""
            if chunk.get("stopped_reason") == "evidence_exhausted" or int(chunk.get("records_seen") or 0) == 0:
                break
        total["cursor_path"] = str(cursor_path)
        return total

    def session_overview(self, *, limit: int = 25) -> dict[str, Any]:
        safe_limit = max(1, min(100, int(limit)))
        records = _load_evidence_records(_evidence_roots(self.settings), limit=5000)
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
                row["branch"] = str(git.get("branch") or row.get("branch") or "")

        contexts = {
            str(node.get("session_id")): _sanitize_output_node(node)
            for node in self.store.list_nodes(kinds=["ContextSnapshot"], limit=max(safe_limit * 4, 100))
            if str(node.get("session_id") or "")
        }
        rows: list[dict[str, Any]] = []
        for session_id, row in sessions.items():
            context = contexts.get(session_id)
            counts = self.store.merge_status(session_id=session_id).get("counts", {})
            rows.append(
                {
                    **{key: value for key, value in row.items() if key != "source_apps"},
                    "source_apps": sorted(row["source_apps"]),
                    "graph_counts": counts,
                    "latest_context": context,
                }
            )
        rows.sort(key=lambda item: str(item.get("latest_at") or ""), reverse=True)
        return {
            "ok": True,
            "graph_status": self.merge_status(),
            "sessions": rows[:safe_limit],
        }

    def session_detail(self, *, session_id: str, limit: int = 120) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        safe_limit = max(1, min(500, int(limit)))
        records = _load_evidence_records(_evidence_roots(self.settings), session_id=session_id, limit=safe_limit)
        nodes = [_sanitize_output_node(node) for node in self.store.list_nodes(session_id=session_id, limit=300)]
        edges = self.store.list_edges(session_id=session_id, limit=500)
        pending = self.pending_evidence(session_id=session_id)
        windows = _reconstruct_clean_windows(records, nodes)
        merge_preview = CommitMergeEngine(self.settings, self.store, self.version_backend, classifier=None).finalize_session(
            session_id=session_id,
            commit="HEAD",
            apply=False,
            limit=300,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "timeline": [_timeline_row(record) for record in records],
            "windows": windows,
            "current_context": self.current_context(session_id=session_id, limit=5),
            "merge_status": self.merge_status(session_id=session_id),
            "merge_preview": merge_preview,
            "pending": {"count": pending.get("count", 0), "cursor_path": pending.get("cursor_path")},
            "graph": {
                "nodes": nodes,
                "edges": edges,
            },
            "central_graph": self.central_graph(limit=80),
        }

    def central_graph(self, *, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        all_nodes = self.store.list_nodes(limit=safe_limit * 8)
        pool = [
            *self.store.list_nodes(status="committed", limit=safe_limit),
            *self.store.list_nodes(status="active", limit=safe_limit),
            *all_nodes,
        ]
        output_ids: set[str] = set()
        nodes: list[dict[str, Any]] = []
        for node in pool:
            node_id = str(node.get("id") or "")
            if node_id in output_ids:
                continue
            if node.get("scope") == "central" or node.get("status") in {"committed", "active"}:
                nodes.append(_sanitize_output_node(node))
                output_ids.add(node_id)
            if len(nodes) >= safe_limit:
                break
        node_by_id = {str(node.get("id") or ""): node for node in all_nodes + pool}
        all_edges = self.store.list_edges(limit=safe_limit * 8)
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
                if edge_id not in edge_ids:
                    central_edges.append(edge)
                    edge_ids.add(edge_id)
                for endpoint_id in (source_id, target_id):
                    if endpoint_id in output_ids:
                        continue
                    endpoint = node_by_id.get(endpoint_id)
                    if not endpoint:
                        continue
                    nodes.append(_sanitize_output_node(endpoint))
                    output_ids.add(endpoint_id)
                    next_frontier.add(endpoint_id)
                    if len(nodes) >= safe_limit:
                        break
                if len(nodes) >= safe_limit or len(central_edges) >= safe_limit * 4:
                    break
            frontier = next_frontier
            if len(nodes) >= safe_limit or len(central_edges) >= safe_limit * 4:
                break
        return {
            "ok": True,
            "nodes": nodes,
            "edges": central_edges[: safe_limit * 4],
            "status": self.merge_status(),
            "warnings": _central_graph_warnings(nodes, central_edges),
        }

    def cleanup_noisy_drafts(self, *, limit: int = 500, apply: bool = False) -> dict[str, Any]:
        candidates = self.store.list_nodes(
            kinds=["ContextSnapshot", "WorkChange", "Decision", "Fix", "Bug", "TestRun"],
            limit=max(1, min(5000, int(limit))),
        )
        noisy = [node for node in candidates if not _is_answer_quality_node(node)]
        changed = 0
        if apply:
            for node in noisy:
                if self.store.set_node_status(str(node["id"]), "abandoned"):
                    changed += 1
        return {
            "ok": True,
            "apply": apply,
            "scanned": len(candidates),
            "noisy_count": len(noisy),
            "changed": changed,
            "nodes": noisy[:50],
        }

    def consolidate_graph(self, *, limit: int = 500, apply: bool = False) -> dict[str, Any]:
        consolidator = DeterministicGraphConsolidator(self.store, project_id=self.settings.project_id)
        return consolidator.consolidate(limit=limit, apply=apply).as_dict()

    def rebuild_graph_cache(self, *, limit: int = 5000) -> dict[str, Any]:
        nodes = self.store.list_nodes(kinds=ANSWER_SEED_KINDS, limit=max(1, min(20000, int(limit))))
        central_nodes = [
            node
            for node in nodes
            if node.get("status") in {"committed", "active"}
            or node.get("scope") == "central"
            or (node.get("status") == "draft" and node.get("kind") == "ContextSnapshot")
        ]
        answer_nodes = _filter_answer_grade_nodes(central_nodes, include_raw=False)
        return self.search_cache.rebuild([_sanitize_output_node(node) for node in answer_nodes])

    def graph_cache_status(self) -> dict[str, Any]:
        return self.search_cache.status()

    def work_trace(self, *, commit: str = "HEAD", cwd: str | Path | None = None) -> dict[str, Any]:
        trace = WorkLedger(self.version_backend).trace_commit(commit=commit, cwd=cwd)
        return {"ok": trace.commit.available, "trace": trace.as_dict()}

    def _new_drain(self) -> EvidenceDrain:
        extractor = QwenGraphExtractor(self.settings)
        builder = SessionGraphBuilder(self.settings, self.store, self.version_backend, extractor=extractor)
        return EvidenceDrain(
            self.settings,
            self.store,
            self.version_backend,
            evidence_roots=_evidence_roots(self.settings),
            builder=builder,
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
        return ["Decision", "Fix", "WorkChange", "ContextSnapshot", "Prompt", "Response", "ToolResult"]
    if intent == "work_history":
        return ["WorkChange", "ContextSnapshot", "GitCommit", "File", "Decision", "Fix", "ToolResult"]
    if intent == "bug_fix_trace":
        return ["Bug", "Fix", "TestRun", "WorkChange", "ContextSnapshot", "GitCommit"]
    if intent == "historical_versions":
        return ["Decision", "Fix", "WorkChange", "ContextSnapshot", "GitCommit"]
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


def _merge_seed_nodes(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for node in primary + secondary:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        existing = merged.get(node_id)
        if existing is None or float(node.get("graph_score") or 0.0) > float(existing.get("graph_score") or 0.0):
            merged[node_id] = node
    return list(merged.values())


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
    if kind == "ContextSnapshot":
        return _is_clean_context_snapshot(node)
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
    terms = [term for term in re.sub(r"[^a-zA-Z0-9_. -]+", " ", query).lower().split() if len(term) > 2]
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        if not include_historical and node.get("status") in {"superseded", "abandoned"}:
            continue
        text = f"{node.get('kind')} {node.get('label')} {node.get('summary')} {json.dumps(node.get('metadata', {}), sort_keys=True)}".lower()
        lexical = sum(1.0 for term in terms if term in text)
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
        ranked.append((lexical + status_bonus + evidence_bonus + float(node.get("graph_score") or 0.0), node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{**node, "score": round(score, 6)} for score, node in ranked]


def _trim_weak_tail_matches(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not nodes:
        return nodes
    top_score = float(nodes[0].get("score") or 0.0)
    if top_score < 4.0:
        return nodes
    floor = max(2.0, top_score - 3.0)
    trimmed = [node for node in nodes if float(node.get("score") or 0.0) >= floor]
    return trimmed or nodes[:1]


def _context_from_snapshots(nodes: list[dict[str, Any]]) -> str:
    if not nodes:
        return "No AMO session context snapshot is available yet. A write/test/git/finalize trigger must be drained first."
    lines = ["AMO current session context."]
    for node in nodes[:5]:
        meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        lines.append(
            "\n".join(
                [
                    f"- node_id={node.get('id')} status={node.get('status')} evidence_id={node.get('evidence_id')}",
                    f"  summary: {node.get('summary') or ''}",
                    f"  goal: {meta.get('goal') or ''}",
                    f"  latest_decision: {meta.get('latest_decision') or ''}",
                    f"  changed_files: {', '.join(meta.get('changed_files') or [])}",
                    f"  tests: {', '.join(meta.get('tests') or [])}",
                    f"  next_step: {meta.get('next_step') or ''}",
                ]
            )
        )
    return "\n".join(lines)


def _is_clean_context_snapshot(node: dict[str, Any]) -> bool:
    if not str(node.get("id") or "").startswith("context:"):
        return False
    summary = str(node.get("summary") or "").strip()
    lowered = summary.lower()
    noisy_prefixes = (
        '"continue":',
        "{",
        "stop:",
        "from __future__",
        "import ",
        "class ",
        "def ",
    )
    noisy_terms = (
        "hook_event_name",
        "manualsmoke",
        "captureonly",
        "status_porcelain",
        "raw_",
    )
    if lowered.startswith(noisy_prefixes):
        return False
    if any(term in lowered for term in noisy_terms):
        return False
    if not _is_clean_answer_summary(summary, node.get("metadata")):
        return False
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return any(
        bool(meta.get(key))
        for key in ("goal", "latest_decision", "changed_files", "tests", "blockers", "next_step")
    ) or bool(summary)


def _is_clean_answer_summary(summary: str, metadata: object = None) -> bool:
    text = summary.strip()
    lowered = text.lower()
    if len(text) < 16:
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
    if len(text) > 240 and _punctuation_ratio(text) > 0.18:
        return False
    if len(text) > 240 and _code_token_ratio(text) > 0.18:
        return False
    meta = metadata if isinstance(metadata, dict) else {}
    if meta.get("trigger", {}).get("trigger_type") == "write" and meta.get("changed_files"):
        return True
    if meta.get("trigger", {}).get("trigger_type") == "write" and not any(
        term in lowered
        for term in ("changed", "implemented", "fixed", "decision", "added", "updated", "removed", "refactor", "test")
    ):
        return False
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
    pending_records: list[dict[str, Any]] = []
    pending_write = False
    windows: list[dict[str, Any]] = []
    for record in records:
        decision = detect_trigger(record, pending_write=pending_write)
        if decision.is_write:
            pending_write = True
        pending_records.append(record)
        if decision.should_process:
            windows.append(_window_row(len(windows) + 1, pending_records, decision, nodes))
            pending_records = []
            pending_write = False
    if pending_records:
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
    version_edges = [edge for edge in edges if edge.get("kind") in {"COMMITTED_AS", "REFINES", "SUPERSEDES", "DUPLICATE_OF", "CONTRADICTS"}]
    if nodes and not version_edges:
        warnings.append("central_graph_has_no_visible_version_edges")
    return warnings


def _is_finalize_boundary(trigger: dict[str, Any]) -> bool:
    return bool(trigger.get("is_commit")) or str(trigger.get("trigger_type") or "") in {
        "explicit_finalize",
        "connector_finalize",
        "stop_finalize",
    }


def _rebuild_smoke(store: GraphStore) -> dict[str, Any]:
    status = store.merge_status()
    counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    nodes = store.list_nodes(limit=5)
    return {
        "ok": bool(nodes) or not any(int(value or 0) for value in counts.values()),
        "status": status,
        "sample_node_count": len(nodes),
    }


def _move_graph_path(source: Path, target: Path) -> None:
    if target.exists():
        _remove_graph_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _remove_graph_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


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
