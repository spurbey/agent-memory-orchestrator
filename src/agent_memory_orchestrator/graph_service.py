from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .adapters import normalize_adapter_event
from .config import Settings
from .evidence_drain import EvidenceDrain
from .graph_store import GraphEdge, GraphNode, GraphStore, KuzuGraphStore
from .qwen_client import DeterministicPlanner, OllamaQwenClient, QwenPlanner, QwenUnavailable
from .raw_evidence import RawEvidenceRef, RawEvidenceStore
from .session_graph import QwenGraphExtractor, SessionGraphBuilder
from .versioning import LocalGitBackend, VersionBackend
from .work_ledger import WorkLedger


HOOK_CONTEXT_EVENTS = {"session_start"}
CAPTURE_ONLY_EVENTS = {"user_prompt_submit", "prompt", "post_tool_use", "tool_result", "stop", "session_stop"}
EVIDENCE_ONLY_KINDS = {"RawEvidenceRef", "Prompt", "ToolUse", "ToolResult", "Turn", "Session", "App", "Repo", "Branch"}


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
        )
        self.version_backend = version_backend or LocalGitBackend()
        self.evidence = evidence_store or RawEvidenceStore(settings.evidence_dir)
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
        kinds = _kinds_for_intent(plan.intent)
        search_started = time.monotonic()
        seed_nodes = self.store.search_nodes(query, limit=max(limit * 4, 20), kinds=kinds)
        raw_requested = bool(include_raw or plan.include_raw)
        expanded = _filter_answer_grade_nodes(_expand_nodes(seed_nodes, self.store), include_raw=raw_requested)
        candidates = _rank_nodes(query, expanded, include_historical=include_historical or plan.include_historical)
        selected = candidates[: max(1, min(50, int(limit)))]
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
            node
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

    def drain_evidence(self, *, limit: int = 500, session_id: str = "") -> dict[str, Any]:
        drain = self._new_drain()
        return drain.drain(limit=max(1, int(limit)), session_id=session_id)

    def pending_evidence(self, *, session_id: str = "") -> dict[str, Any]:
        drain = self._new_drain()
        return drain.pending(session_id=session_id)

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
        return ["Decision", "Fix", "WorkChange", "Prompt", "Response", "ToolResult"]
    if intent == "work_history":
        return ["WorkChange", "GitCommit", "File", "Decision", "Fix", "ToolResult"]
    if intent == "bug_fix_trace":
        return ["Bug", "Fix", "TestRun", "WorkChange", "GitCommit"]
    if intent == "historical_versions":
        return ["Decision", "Fix", "WorkChange", "GitCommit"]
    return None


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
        if node.get("kind") == "ContextSnapshot" and not _is_clean_context_snapshot(node):
            continue
        filtered.append(node)
    return filtered


def _rank_nodes(query: str, nodes: list[dict[str, Any]], *, include_historical: bool) -> list[dict[str, Any]]:
    terms = [term for term in re.sub(r"[^a-zA-Z0-9_. -]+", " ", query).lower().split() if len(term) > 2]
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        if not include_historical and node.get("status") in {"superseded", "abandoned"}:
            continue
        text = f"{node.get('kind')} {node.get('label')} {node.get('summary')} {json.dumps(node.get('metadata', {}), sort_keys=True)}".lower()
        lexical = sum(1.0 for term in terms if term in text)
        status_bonus = 1.0 if node.get("status") in {"active", "committed"} else 0.25
        evidence_bonus = 0.5 if node.get("evidence_id") else 0.0
        ranked.append((lexical + status_bonus + evidence_bonus + float(node.get("graph_score") or 0.0), node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{**node, "score": round(score, 6)} for score, node in ranked]


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
    summary = str(node.get("summary") or "").strip().lower()
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
    if summary.startswith(noisy_prefixes):
        return False
    if any(term in summary for term in noisy_terms):
        return False
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return any(
        bool(meta.get(key))
        for key in ("goal", "latest_decision", "changed_files", "tests", "blockers", "next_step")
    ) or bool(summary)


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
