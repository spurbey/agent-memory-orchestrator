from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....core.db import connect
from ....graph.service import GraphRagService
from ....graph.store import KuzuGraphStore
from ....infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ....infrastructure.kuzu.central_graph import repo_central_graph_path
from ....application.services.retrieval_query import retrieve_session_graph as retrieve_indexed_docs
from ....reasoning_graph.session_runtime import DEFAULT_CODE_EMBEDDING_MODEL
from ....reasoning_graph.session_runtime import SessionGraphBuildOptions
from ....reasoning_graph.session_runtime import SessionGraphQueryOptions
from ....reasoning_graph.session_runtime import build_and_query_session_graph
from ....reasoning_graph.session_runtime import build_session_graph
from ....reasoning_graph.session_runtime import default_session_graph_path
from ....reasoning_graph.session_runtime import query_session_graph
from ...daemon.client import DaemonClient, DaemonUnavailable

GRAPH_COMMANDS = (
    "graph-search",
    "graph-status",
    "graph-drain",
    "graph-retrieval-build",
    "graph-retrieval-embed",
    "graph-retrieve",
    "graph-version-flow",
    "graph-build-session",
    "graph-session-search",
)


def add_graph_subcommands(sub: Any) -> None:
    graph_search = sub.add_parser("graph-search", help="Explicit Kuzu GraphRAG search")
    graph_search.add_argument("--query", required=True)
    graph_search.add_argument("--limit", type=int, default=8)
    graph_search.add_argument("--include-raw", action="store_true")
    graph_search.add_argument("--include-historical", action="store_true")
    graph_search.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_status = sub.add_parser("graph-status", help="Inspect Kuzu graph merge status")
    graph_status.add_argument("--session-id", default="")
    graph_status.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_drain = sub.add_parser("graph-drain", help="Daemon drains captured evidence into the Kuzu session graph")
    graph_drain.add_argument("--session-id", default="")
    graph_drain.add_argument("--limit", type=int, default=500)
    graph_drain.add_argument("--max-windows", type=int, default=None, help="Maximum Qwen trigger windows to process in one request.")
    graph_drain.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieval_build = sub.add_parser("graph-retrieval-build", help="Build SQLite/FTS retrieval docs from the graph")
    graph_retrieval_build.add_argument("--session-id", default="")
    graph_retrieval_build.add_argument("--repo-id", default="", help="Limit retrieval docs to one canonical repo id.")
    graph_retrieval_build.add_argument("--limit", type=int, default=10000)
    graph_retrieval_build.add_argument("--max-doc-chars", type=int, default=5000)
    graph_retrieval_build.add_argument("--db-path", type=Path, default=None)
    graph_retrieval_build.add_argument("--graph-path", type=Path, default=None)
    graph_retrieval_build.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieval_embed = sub.add_parser("graph-retrieval-embed", help="Resume embedding missing graph retrieval docs")
    graph_retrieval_embed.add_argument("--session-id", default="")
    graph_retrieval_embed.add_argument("--repo-id", default="", help="Limit embedding work to one canonical repo id.")
    graph_retrieval_embed.add_argument("--limit", type=int, default=100)
    graph_retrieval_embed.add_argument("--model", default="")
    graph_retrieval_embed.add_argument("--graph-scope", default="")
    graph_retrieval_embed.add_argument("--db-path", type=Path, default=None)
    graph_retrieval_embed.add_argument("--graph-path", type=Path, default=None)
    graph_retrieval_embed.add_argument("--no-faiss", action="store_true", help="Do not rebuild the FAISS cache after embedding.")
    graph_retrieval_embed.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieve = sub.add_parser("graph-retrieve", help="Retrieve over graph docs with exact/BM25/vector/Kuzu expansion")
    graph_retrieve.add_argument("--query", required=True)
    graph_retrieve.add_argument("--session-id", default="")
    graph_retrieve.add_argument("--repo-id", default="", help="Search one canonical repo id.")
    graph_retrieve.add_argument("--limit", type=int, default=8)
    graph_retrieve.add_argument("--model", default="")
    graph_retrieve.add_argument("--graph-scope", default="")
    graph_retrieve.add_argument("--db-path", type=Path, default=None)
    graph_retrieve.add_argument("--graph-path", type=Path, default=None)
    graph_retrieve.add_argument("--no-vector", action="store_true")
    graph_retrieve.add_argument("--require-vector", action="store_true", help="Fail instead of falling back if vector retrieval returns no candidates.")
    graph_retrieve.add_argument("--no-answer", action="store_true")
    graph_retrieve.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_version_flow = sub.add_parser("graph-version-flow", help="Show commit-centric graph versioning flow")
    graph_version_flow.add_argument("--commit", default="", help="Commit SHA/prefix to inspect. Omit to list recent flows.")
    graph_version_flow.add_argument("--session-id", default="", help="Restrict version flow to one AMO session.")
    graph_version_flow.add_argument("--repo-id", default="", help="Limit version flow to one canonical repo id.")
    graph_version_flow.add_argument("--limit", type=int, default=100)
    graph_version_flow.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_build_session = sub.add_parser(
        "graph-build-session",
        help="Build an isolated production session graph from real AMO evidence and Codex transcripts",
    )
    graph_build_session.add_argument("--session-id", required=True)
    graph_build_session.add_argument("--commit", required=True)
    graph_build_session.add_argument("--repo-root", type=Path, default=Path.cwd())
    graph_build_session.add_argument("--graph-path", type=Path, default=None)
    graph_build_session.add_argument("--evidence-path", action="append", type=Path, default=[])
    graph_build_session.add_argument("--transcript-path", action="append", type=Path, default=[])
    graph_build_session.add_argument("--file-path", action="append", default=[])
    graph_build_session.add_argument("--query", default="")
    graph_build_session.add_argument("--code-query", default="")
    graph_build_session.add_argument("--limit", type=int, default=8)
    graph_build_session.add_argument("--limit-events", type=int, default=None)
    graph_build_session.add_argument("--force", action="store_true", help="Replace the target session graph path.")
    graph_build_session.add_argument("--text-embedding-model", default="")
    graph_build_session.add_argument("--code-embedding-model", default=DEFAULT_CODE_EMBEDDING_MODEL)

    graph_session_search = sub.add_parser("graph-session-search", help="Search an isolated production session graph")
    graph_session_search.add_argument("--graph-path", required=True, type=Path)
    graph_session_search.add_argument("--query", default="")
    graph_session_search.add_argument("--code-query", default="")
    graph_session_search.add_argument("--limit", type=int, default=8)
    graph_session_search.add_argument("--text-embedding-model", default="")
    graph_session_search.add_argument("--code-embedding-model", default=DEFAULT_CODE_EMBEDDING_MODEL)


class _NoGraphWalkStore:
    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, object]]:
        raise AssertionError("index-only graph retrieval should not expand Kuzu neighbors")

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, object]]:
        raise AssertionError("index-only graph retrieval should not load Kuzu nodes")


def _retrieve_index_only(settings: Settings, args: Any) -> dict[str, Any]:
    target_db = args.db_path or settings.retrieval_db_path
    conn = connect(target_db)
    try:
        index = RetrievalIndexStore(conn)
        if args.repo_id and not index.active_projection_id(args.repo_id):
            return {
                "ok": False,
                "error": "active_projection_missing",
                "repo_id": args.repo_id,
                "db_path": str(target_db),
                "mode": "index_only",
            }
        result = retrieve_indexed_docs(
            query=args.query,
            index_store=index,
            graph_store=_NoGraphWalkStore(),
            session_id=args.session_id,
            repo_id=args.repo_id,
            limit=max(1, min(50, int(args.limit))),
            expand_neighbors=0,
            include_graph_nodes=False,
            graph_scope=args.graph_scope or settings.retrieval_graph_scope,
            require_vector=getattr(args, "require_vector", False),
        )
        return {
            "ok": True,
            "db_path": str(target_db),
            "graph_path": str(settings.graph_path),
            "graph_scope": args.graph_scope or settings.retrieval_graph_scope,
            "retrieval": result.as_dict(),
            "mode": "index_only",
        }
    finally:
        conn.close()


def _settings_with_path_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    updates = {}
    db_path = getattr(args, "db_path", None)
    graph_path = getattr(args, "graph_path", None)
    retrieval_command = getattr(args, "command", "") in {
        "graph-retrieval-build",
        "graph-retrieval-embed",
        "graph-retrieve",
    }
    if db_path:
        updates["db_path"] = Path(db_path).expanduser().resolve()
    if graph_path:
        updates["graph_path"] = Path(graph_path).expanduser().resolve()
    elif retrieval_command and settings.retrieval_graph_path is not None:
        updates["graph_path"] = settings.retrieval_graph_path
    if not updates:
        return settings
    for key in ("db_path", "graph_path"):
        path = updates.get(key)
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
    return replace(settings, **updates)


def handle_graph_command(args: argparse.Namespace, *, emit: Callable[[object], None]) -> int | None:
    """Run graph and graph retrieval CLI commands."""
    if args.command == "graph-build-session":
        settings = Settings.load()
        graph_path = args.graph_path or default_session_graph_path(args.session_id)
        build_options = SessionGraphBuildOptions(
            session_id=args.session_id,
            graph_path=graph_path,
            repo_root=args.repo_root,
            commit=args.commit,
            evidence_paths=tuple(args.evidence_path or ()),
            transcript_paths=tuple(args.transcript_path or ()),
            file_paths=tuple(args.file_path or ()),
            text_embedding_model=args.text_embedding_model or settings.embedding_model,
            code_embedding_model=args.code_embedding_model or DEFAULT_CODE_EMBEDDING_MODEL,
            force=args.force,
            limit_events=args.limit_events,
        )
        if args.query or args.code_query:
            emit(
                build_and_query_session_graph(
                    build_options,
                    query=args.query or None,
                    code_query=args.code_query or None,
                    limit=args.limit,
                )
            )
        else:
            emit({"ok": True, "build": asdict(build_session_graph(build_options))})
        return 0

    if args.command == "graph-session-search":
        settings = Settings.load()
        result = query_session_graph(
            SessionGraphQueryOptions(
                graph_path=args.graph_path,
                query=args.query or None,
                code_query=args.code_query or None,
                text_embedding_model=args.text_embedding_model or settings.embedding_model,
                code_embedding_model=args.code_embedding_model or DEFAULT_CODE_EMBEDDING_MODEL,
                limit=args.limit,
            )
        )
        emit({"ok": True, "result": asdict(result)})
        return 0

    if args.command not in {
        "graph-search",
        "graph-status",
        "graph-drain",
        "graph-retrieval-build",
        "graph-retrieval-embed",
        "graph-retrieve",
        "graph-version-flow",
    }:
        return None

    settings = _settings_with_path_overrides(Settings.load(), args)
    if args.offline:
        if args.command == "graph-retrieve" and args.no_answer and args.no_vector:
            emit(_retrieve_index_only(settings, args))
            return 0
        graph_settings = settings
        graph_store = None
        read_only_graph = args.command in {
            "graph-search",
            "graph-retrieval-build",
            "graph-retrieval-embed",
            "graph-retrieve",
            "graph-version-flow",
        }
        if args.command in {"graph-retrieve", "graph-version-flow"} and str(args.repo_id or "").strip():
            central_graph_path = repo_central_graph_path(settings, args.repo_id)
            graph_settings = replace(settings, graph_path=central_graph_path)
            graph_store = KuzuGraphStore(central_graph_path, read_only=True)
        graph = GraphRagService(graph_settings, store=graph_store, read_only=read_only_graph)
        try:
            if args.command == "graph-search":
                result = graph.graph_search(
                    query=args.query,
                    limit=args.limit,
                    include_raw=args.include_raw,
                    include_historical=args.include_historical,
                )
            elif args.command == "graph-drain":
                result = graph.drain_evidence(limit=args.limit, session_id=args.session_id, max_windows=args.max_windows)
            elif args.command == "graph-retrieval-build":
                result = graph.rebuild_retrieval_index(
                    db_path=args.db_path,
                    session_id=args.session_id,
                    repo_id=args.repo_id,
                    limit=args.limit,
                    max_doc_chars=args.max_doc_chars,
                )
            elif args.command == "graph-retrieval-embed":
                result = graph.embed_retrieval_index(
                    db_path=args.db_path,
                    session_id=args.session_id,
                    repo_id=args.repo_id,
                    limit=args.limit,
                    model=args.model,
                    graph_scope=args.graph_scope,
                    rebuild_faiss=not args.no_faiss,
                )
            elif args.command == "graph-retrieve":
                result = graph.retrieve_indexed_graph(
                    query=args.query,
                    db_path=args.db_path,
                    session_id=args.session_id,
                    repo_id=args.repo_id,
                    limit=args.limit,
                    use_vector=not args.no_vector,
                    model=args.model,
                    graph_scope=args.graph_scope,
                    require_vector=args.require_vector,
                    include_answer=not args.no_answer,
                )
            elif args.command == "graph-version-flow":
                result = graph.version_flow(commit=args.commit, session_id=args.session_id, repo_id=args.repo_id, limit=args.limit)
            else:
                result = graph.merge_status(session_id=args.session_id)
            emit(result)
        finally:
            graph.close()
        return 0

    client_timeout = (
        300
        if args.command
        in {
            "graph-drain",
            "graph-retrieval-build",
            "graph-retrieval-embed",
            "graph-version-flow",
        }
        else 60
    )
    client = DaemonClient.from_settings(settings, timeout_seconds=client_timeout)
    try:
        if args.command == "graph-search":
            result = client.post(
                "/graph/search",
                {
                    "query": args.query,
                    "limit": args.limit,
                    "include_raw": args.include_raw,
                    "include_historical": args.include_historical,
                },
            )
        elif args.command == "graph-drain":
            result = client.post(
                "/graph/drain",
                {"session_id": args.session_id, "limit": args.limit, "max_windows": args.max_windows},
            )
        elif args.command == "graph-retrieval-build":
            result = client.post(
                "/graph/retrieval-build",
                {
                    "session_id": args.session_id,
                    "repo_id": args.repo_id,
                    "limit": args.limit,
                    "max_doc_chars": args.max_doc_chars,
                    "db_path": str(args.db_path) if args.db_path else "",
                    "graph_path": str(args.graph_path) if args.graph_path else "",
                },
            )
        elif args.command == "graph-retrieval-embed":
            result = client.post(
                "/graph/retrieval-embed",
                {
                    "session_id": args.session_id,
                    "repo_id": args.repo_id,
                    "limit": args.limit,
                    "model": args.model,
                    "graph_scope": args.graph_scope,
                    "db_path": str(args.db_path) if args.db_path else "",
                    "graph_path": str(args.graph_path) if args.graph_path else "",
                    "rebuild_faiss": not args.no_faiss,
                },
            )
        elif args.command == "graph-retrieve":
            result = client.post(
                "/graph/retrieve",
                {
                    "query": args.query,
                    "session_id": args.session_id,
                    "repo_id": args.repo_id,
                    "limit": args.limit,
                    "model": args.model,
                    "graph_scope": args.graph_scope,
                    "db_path": str(args.db_path) if args.db_path else "",
                    "graph_path": str(args.graph_path) if args.graph_path else "",
                    "use_vector": not args.no_vector,
                    "require_vector": args.require_vector,
                    "include_answer": not args.no_answer,
                },
            )
        elif args.command == "graph-version-flow":
            result = client.post(
                "/graph/version-flow",
                {"commit": args.commit, "session_id": args.session_id, "repo_id": args.repo_id, "limit": args.limit},
            )
        else:
            result = client.get("/api/graph/status", {"session_id": args.session_id})
    except DaemonUnavailable as exc:
        emit(
            {
                "ok": False,
                "requires_daemon": True,
                "error": str(exc),
                "hint": "Start the daemon with: amo-daemon",
            }
        )
        return 1
    emit(result)
    return 0


__all__ = ["GRAPH_COMMANDS", "add_graph_subcommands", "handle_graph_command"]
