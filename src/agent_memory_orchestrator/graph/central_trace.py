from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from typing import Iterable

from ..core.config import Settings
from ..core.db import connect
from ..domain.retrieval.answer_trace import build_central_answer_trace
from ..infrastructure.kuzu import GraphStore


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
