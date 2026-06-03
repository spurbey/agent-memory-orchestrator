from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....domain.evidence.events import CAPTURE_ONLY_EVENTS
from ....domain.evidence.events import HOOK_CONTEXT_EVENTS
from ....evidence.raw_store import RawEvidenceRef, RawEvidenceStore
from ....infrastructure.kuzu import GraphEdge, GraphNode, GraphStore
from ....integrations.adapters import normalize_adapter_event
from ....versioning import VersionBackend


def capture_hook(
    *,
    settings: Settings,
    graph_store: GraphStore,
    evidence_store: RawEvidenceStore,
    version_backend: VersionBackend,
    startup_context: Callable[..., str],
    payload: dict[str, Any],
    default_agent: str = "codex",
) -> dict[str, Any]:
    normalized = normalize_adapter_event(payload, default_agent=default_agent) or fallback_event(payload, default_agent)
    session_id = str(normalized["session_id"])
    event_type = str(normalized["event_type"])
    source_app = str(normalized["source_app"])
    evidence = evidence_store.append(payload, session_id=session_id, source_app=source_app, event_name=event_type)
    cwd = event_cwd(payload, normalized)
    git = version_backend.snapshot(cwd)

    git_raw = git.as_dict()
    git_compact = compact_git(git_raw)
    upsert_basic_nodes(settings=settings, graph_store=graph_store, normalized=normalized, evidence=evidence, git=git_compact)
    merge = auto_merge_if_commit_event(
        settings=settings,
        graph_store=graph_store,
        normalized=normalized,
        evidence=evidence,
        git=git_raw,
    )

    context = ""
    if event_type in HOOK_CONTEXT_EVENTS:
        context = startup_context(session_id=session_id, source_app=source_app, git=git_compact)
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


def upsert_basic_nodes(
    *,
    settings: Settings,
    graph_store: GraphStore,
    normalized: dict[str, Any],
    evidence: RawEvidenceRef,
    git: dict[str, Any],
) -> None:
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
        project_id=settings.project_id,
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
        project_id=settings.project_id,
        source_app=source_app,
        evidence_id=evidence.id,
        metadata=evidence.as_dict(),
    )
    event_node = GraphNode(
        id=f"event:{evidence.id}",
        kind=node_kind_for_event(event_type),
        label=label_for_event(event_type, content),
        summary=summarize_event(event_type, content),
        status="draft",
        scope="session",
        session_id=session_id,
        project_id=settings.project_id,
        source_app=source_app,
        evidence_id=evidence.id,
        metadata={"event_type": event_type, **metadata, "git": git},
    )
    for node in (session_node, app_node, evidence_node, event_node):
        graph_store.upsert_node(node)
    upsert_edge(graph_store, session_node.id, app_node.id, "PART_OF", evidence.id)
    upsert_edge(graph_store, session_node.id, event_node.id, "HAS_TURN", evidence.id)
    upsert_edge(graph_store, event_node.id, evidence_node.id, "EVIDENCED_BY", evidence.id)

    if git.get("available"):
        repo_id = f"repo:{git.get('repo_root')}"
        branch_id = f"branch:{git.get('repo_root')}:{git.get('branch')}"
        graph_store.upsert_node(
            GraphNode(
                id=repo_id,
                kind="Repo",
                label=str(git.get("repo_root")),
                summary=f"Local Git repo {git.get('repo_root')}",
                status="active",
                scope="central",
                project_id=settings.project_id,
                metadata=git,
            )
        )
        graph_store.upsert_node(
            GraphNode(
                id=branch_id,
                kind="Branch",
                label=str(git.get("branch")),
                summary=f"Branch {git.get('branch')} in {git.get('repo_root')}",
                status="active",
                scope="central",
                project_id=settings.project_id,
                commit_id=str(git.get("head") or ""),
                metadata=git,
            )
        )
        upsert_edge(graph_store, session_node.id, repo_id, "PART_OF", evidence.id)
        upsert_edge(graph_store, branch_id, repo_id, "PART_OF", evidence.id)


def auto_merge_if_commit_event(
    *,
    settings: Settings,
    graph_store: GraphStore,
    normalized: dict[str, Any],
    evidence: RawEvidenceRef,
    git: dict[str, Any],
) -> dict[str, Any]:
    if not git.get("available") or not git.get("head"):
        return {"merged": False, "reason": "git_unavailable"}
    content = str(normalized.get("content") or "")
    metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
    if not looks_like_commit_event(content, metadata):
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
        project_id=settings.project_id,
        source_app=str(normalized["source_app"]),
        evidence_id=evidence.id,
        commit_id=commit,
        metadata=git,
    )
    graph_store.upsert_node(commit_node)
    upsert_edge(graph_store, f"session:{session_id}", commit_node.id, "MERGED_INTO", evidence.id)
    upsert_edge(graph_store, f"event:{evidence.id}", commit_node.id, "COMMITTED_AS", evidence.id)
    return {"merged": True, "commit": commit}


def upsert_edge(graph_store: GraphStore, source: str, target: str, kind: str, evidence_id: str) -> None:
    graph_store.upsert_edge(
        GraphEdge(
            id=f"edge:{source}:{kind}:{target}",
            source_id=source,
            target_id=target,
            kind=kind,
            evidence_id=evidence_id,
        )
    )


def fallback_event(payload: dict[str, Any], default_agent: str) -> dict[str, Any]:
    event_name = snake(str(payload.get("hook_event_name") or payload.get("event_type") or "message"))
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


def event_cwd(payload: dict[str, Any], normalized: dict[str, Any]) -> str | Path | None:
    metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
    return payload.get("cwd") or metadata.get("cwd") or os.getenv("AMO_WORKSPACE_CWD") or None


def node_kind_for_event(event_type: str) -> str:
    if event_type in {"prompt", "user_prompt_submit"}:
        return "Prompt"
    if "tool" in event_type:
        return "ToolResult"
    if "response" in event_type:
        return "Response"
    return "Turn"


def label_for_event(event_type: str, content: str) -> str:
    first = " ".join(content.strip().split())[:96]
    return first or event_type


def summarize_event(event_type: str, content: str) -> str:
    clean = " ".join(str(content or "").split())
    if len(clean) > 360:
        clean = clean[:357] + "..."
    return f"{event_type}: {clean}" if clean else event_type


def looks_like_commit_event(content: str, metadata: dict[str, Any]) -> bool:
    lowered = f"{content}\n{json.dumps(metadata, sort_keys=True)}".lower()
    return "git commit" in lowered or bool(re.search(r"\[[^\]]+ [0-9a-f]{7,}\]", lowered))


def snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def compact_git(git: dict[str, Any]) -> dict[str, Any]:
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


__all__ = ["capture_hook"]
