from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .daemon_client import DaemonClient, DaemonUnavailable
from .graph_diagnostics import debug_hooks, debug_qwen
from .graph_service import GraphRagService
from .graph_store import GraphBackendUnavailable
from .install_service import InstallOptions
from .install_service import apply_install_plan
from .install_service import build_install_plan
from .install_service import doctor as install_doctor
from .install_service import uninstall as uninstall_targets
from .memory_service import MemoryService
from .model_manager import download_models, list_model_presets, model_status, preflight_models
from .orchestrator import OrchestratorService
from .privacy import redact_secrets
from .qwen_client import QwenUnavailable


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize local database schema")
    sub.add_parser("init-graph", help="Initialize local Kuzu GraphRAG schema")

    install = sub.add_parser("install", help="Configure Claude/Codex hooks, MCP, and local AMO runtime config")
    install.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    install.add_argument("--user-home", type=Path, default=Path.home(), help="Home directory containing .codex/.claude")
    install.add_argument(
        "--amo-home",
        type=Path,
        default=Path.home() / ".agent-memory-orchestrator",
        help="AMO data/config home used by hooks and MCP.",
    )
    _add_model_selection_args(install)
    install.add_argument(
        "--python-command",
        default=sys.executable or "python",
        help="Python executable visible to Claude/Codex hooks.",
    )
    install.add_argument("--download-models", action="store_true", help="Download selected local models during install.")
    install.add_argument("--skip-init-db", action="store_true", help="Do not initialize the AMO SQLite database.")
    install.add_argument("--dry-run", action="store_true", help="Show planned changes without writing files.")
    install.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")
    install.add_argument("--force", action="store_true", help="Overwrite existing AMO target entries when safe.")

    doctor_cmd = sub.add_parser("doctor", help="Check AMO install/config status")
    doctor_cmd.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    doctor_cmd.add_argument("--user-home", type=Path, default=Path.home())
    doctor_cmd.add_argument("--amo-home", type=Path, default=Path.home() / ".agent-memory-orchestrator")

    uninstall_cmd = sub.add_parser("uninstall", help="Remove AMO-managed Claude/Codex config entries")
    uninstall_cmd.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    uninstall_cmd.add_argument("--user-home", type=Path, default=Path.home())
    uninstall_cmd.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")

    ingest = sub.add_parser("ingest-transcript", help="Ingest JSONL transcript")
    ingest.add_argument("--agent", required=True, choices=["claude", "codex", "user", "system"])
    ingest.add_argument("--file", required=True, type=Path)
    ingest.add_argument("--session-id", required=True)
    ingest.add_argument("--session-title")

    hook = sub.add_parser("ingest-hook", help="Ingest one Claude/Codex hook JSON payload")
    hook.add_argument("--agent", default="codex", choices=["claude", "codex", "user", "system"])
    hook.add_argument("--file", required=True, type=Path)

    codex_import = sub.add_parser("import-codex-sessions", help="Import Codex rollout JSONL sessions")
    codex_import.add_argument("--root", type=Path, default=Path.home() / ".codex" / "sessions")
    codex_import.add_argument("--limit", type=int, default=30)
    codex_import.add_argument("--defer-vectors", action="store_true", help="Skip embeddings during import; run rebuild-indexes later.")
    codex_import.add_argument(
        "--include-existing",
        action="store_true",
        help="Reprocess sessions that already have imported events. Default skips them to avoid duplicates.",
    )

    clean = sub.add_parser("rebuild-clean-db", help="Create a fresh DB from raw Codex sessions")
    clean.add_argument("--out", required=True, type=Path)
    clean.add_argument("--codex-root", type=Path, default=Path.home() / ".codex" / "sessions")
    clean.add_argument("--limit", type=int, default=30)
    clean.add_argument("--force", action="store_true")

    sub.add_parser("print-codex-hooks", help="Print a Codex config.toml snippet for AMO capture-only hooks")

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("--query", required=True)
    search.add_argument("--session-id")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-historical", action="store_true")

    context = sub.add_parser("context-pack", help="Build an agent-ready memory context pack")
    context.add_argument("--query", required=True)
    context.add_argument("--session-id")
    context.add_argument("--budget", type=int, default=None)
    context.add_argument("--limit", type=int, default=12)
    context.add_argument("--include-historical", action="store_true")
    context.add_argument("--format", choices=["json", "text"], default="json")

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
    graph_drain.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    debug = sub.add_parser("debug", help="Debug AMO hook, drain, Qwen, graph, and retrieval stages")
    debug_sub = debug.add_subparsers(dest="debug_command", required=True)
    debug_sub.add_parser("hooks", help="Check hook config, log, and latest evidence")
    debug_drain = debug_sub.add_parser("drain", help="Show pending drain cursor/evidence state")
    debug_drain.add_argument("--session-id", default="")
    debug_qwen_cmd = debug_sub.add_parser("qwen", help="Check Qwen availability and query-planner JSON")
    debug_qwen_cmd.add_argument("--sample", default="what did we decide about codex hooks")
    debug_graph_cmd = debug_sub.add_parser("graph", help="Show graph status and current context")
    debug_graph_cmd.add_argument("--session-id", default="")
    debug_retrieval = debug_sub.add_parser("retrieval", help="Show retrieval output through daemon")
    debug_retrieval.add_argument("--query", required=True)
    debug_retrieval.add_argument("--limit", type=int, default=8)

    timeline = sub.add_parser("timeline", help="View session timeline")
    timeline.add_argument("--session-id", required=True)
    timeline.add_argument("--limit", type=int, default=50)

    export_cmd = sub.add_parser("export", help="Export snapshot to JSONL")
    export_cmd.add_argument("--out", required=True, type=Path)
    export_cmd.add_argument("--session-id")

    import_cmd = sub.add_parser("import", help="Import snapshot JSONL")
    import_cmd.add_argument("--file", required=True, type=Path)

    summary = sub.add_parser("session-summary", help="Generate deterministic session summary")
    summary.add_argument("--session-id", required=True)

    sub.add_parser("metrics", help="Inspect pipeline/retrieval row counts and latest retrieval")
    rebuild = sub.add_parser("rebuild-indexes", help="Rebuild FTS/vector index rows from canonical memory_units")
    rebuild.add_argument("--force-vectors", action="store_true")

    models = sub.add_parser("models", help="Manage local embedding/reranker models")
    model_sub = models.add_subparsers(dest="models_command", required=True)
    model_sub.add_parser("list", help="List hardware-aware model presets")
    model_status_cmd = model_sub.add_parser("status", help="Check whether selected models are cached locally")
    _add_model_selection_args(model_status_cmd)
    model_status_cmd.add_argument("--load-check", action="store_true", help="Also try loading models with local_files_only")
    model_download = model_sub.add_parser("download", help="Intentionally download/cache selected models once")
    _add_model_selection_args(model_download)
    model_download.add_argument("--cache-dir", type=Path)
    model_preflight = model_sub.add_parser("preflight", help="Require selected models to load from local cache")
    _add_model_selection_args(model_preflight)

    orch_start = sub.add_parser("orchestrate-start", help="Start orchestrator session")
    orch_start.add_argument("--session-id", required=True)
    orch_start.add_argument("--title")

    orch_submit = sub.add_parser("orchestrate-submit", help="Submit orchestrator round")
    orch_submit.add_argument("--session-id", required=True)
    orch_submit.add_argument("--agent", required=True, choices=["claude", "codex"])
    orch_submit.add_argument("--summary", required=True)
    orch_submit.add_argument("--confidence", required=True, type=float)
    orch_submit.add_argument("--artifact-uri", default="")
    orch_submit.add_argument("--blocking-issue", action="append", default=[])

    orch_status = sub.add_parser("orchestrate-status", help="Get orchestrator status")
    orch_status.add_argument("--session-id", required=True)

    orch_decide = sub.add_parser("orchestrate-decision", help="Apply user decision")
    orch_decide.add_argument("--session-id", required=True)
    orch_decide.add_argument("--decision", required=True, choices=["approved", "rejected"])
    orch_decide.add_argument("--notes", default="")
    orch_decide.add_argument("--decided-by", default="user")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            options = InstallOptions(
                target=args.target,
                user_home=args.user_home,
                amo_home=args.amo_home,
                preset=args.preset,
                embedding_model=args.embedding_model,
                reranker_model=args.reranker_model,
                qwen_model=args.qwen_model,
                python_command=args.python_command,
                force=args.force,
            )
            plan = build_install_plan(options)
            summary = _summarize_install_plan(plan)
            if args.dry_run:
                _print({"ok": True, "dry_run": True, "plan": summary})
                return 0
            if not args.yes:
                _print({"ok": True, "pending_plan": summary})
                if not _confirm("Apply AMO install changes?"):
                    _print({"ok": False, "cancelled": True, "plan": summary})
                    return 1
            result = apply_install_plan(plan)
            model_result = None
            if args.download_models:
                model_result = download_models(
                    preset=args.preset,
                    embedding_model=args.embedding_model,
                    reranker_model=args.reranker_model,
                    qwen_model=args.qwen_model,
                )
            init_result = None
            init_graph = None
            if not args.skip_init_db:
                os.environ["AMO_HOME"] = plan["amo_home"]
                init_settings = Settings.load()
                svc = MemoryService(init_settings)
                try:
                    svc.init_db()
                    init_result = {"db_path": str(init_settings.db_path)}
                finally:
                    svc.close()
                try:
                    graph = GraphRagService(init_settings)
                    graph.close()
                    init_graph = {"ok": True, "graph_path": str(init_settings.graph_path)}
                except GraphBackendUnavailable as exc:
                    init_graph = {"ok": False, "error": str(exc)}
            _print(
                {
                    "ok": True,
                    "plan": summary,
                    "apply": result,
                    "models": model_result,
                    "init_db": init_result,
                    "init_graph": init_graph,
                }
            )
            return 0

        if args.command == "doctor":
            result = install_doctor(target=args.target, user_home=args.user_home, amo_home=args.amo_home)
            _print(result)
            return 0 if result["ok"] else 1

        if args.command == "uninstall":
            if not args.yes and not _confirm("Remove AMO-managed config entries?"):
                _print({"ok": False, "cancelled": True})
                return 1
            _print(uninstall_targets(target=args.target, user_home=args.user_home))
            return 0

        if args.command == "init-db":
            settings = Settings.load()
            svc = MemoryService(settings)
            try:
                svc.init_db()
            finally:
                svc.close()
            _print({"ok": True, "db_path": str(settings.db_path)})
            return 0

        if args.command == "init-graph":
            settings = Settings.load()
            graph = GraphRagService(settings)
            try:
                _print({"ok": True, "graph_path": str(settings.graph_path), "backend": settings.graph_backend})
            finally:
                graph.close()
            return 0

        if args.command == "models":
            if args.models_command == "list":
                _print({"ok": True, "presets": list_model_presets()})
            elif args.models_command == "status":
                _print(
                    {
                        "ok": True,
                        "result": model_status(
                            preset=args.preset,
                            embedding_model=args.embedding_model,
                            reranker_model=args.reranker_model,
                            qwen_model=args.qwen_model,
                            load_check=args.load_check,
                        ),
                    }
                )
            elif args.models_command == "download":
                _print(
                    {
                        "ok": True,
                        "result": download_models(
                            preset=args.preset,
                            embedding_model=args.embedding_model,
                            reranker_model=args.reranker_model,
                            qwen_model=args.qwen_model,
                            cache_dir=args.cache_dir,
                        ),
                    }
                )
            elif args.models_command == "preflight":
                result = preflight_models(
                    preset=args.preset,
                    embedding_model=args.embedding_model,
                    reranker_model=args.reranker_model,
                    qwen_model=args.qwen_model,
                )
                _print({"ok": result["ok"], "result": result})
                return 0 if result["ok"] else 1
            return 0

        if args.command in {
            "ingest-transcript",
            "ingest-hook",
            "import-codex-sessions",
            "rebuild-clean-db",
            "search",
            "context-pack",
            "timeline",
            "export",
            "import",
            "session-summary",
            "metrics",
            "rebuild-indexes",
            "print-codex-hooks",
        }:
            if args.command == "rebuild-clean-db":
                settings = Settings.load()
                result = _rebuild_clean_db(settings, args.out, args.codex_root, args.limit, args.force)
                _print({"ok": True, "result": result})
                return 0

            settings = Settings.load()
            svc = MemoryService(settings)
            try:
                svc.init_db()
                if args.command == "ingest-transcript":
                    result = svc.ingest_transcript(
                        agent=args.agent,
                        file_path=args.file,
                        session_id=args.session_id,
                        session_title=args.session_title,
                    )
                    _print({"ok": True, **result})
                elif args.command == "ingest-hook":
                    payload = json.loads(args.file.read_text(encoding="utf-8"))
                    result = svc.ingest_hook_payload(payload, default_agent=args.agent)
                    _print({"ok": True, **result})
                elif args.command == "import-codex-sessions":
                    result = svc.import_codex_sessions(
                        args.root,
                        limit=args.limit,
                        defer_vectors=args.defer_vectors,
                        skip_existing=not args.include_existing,
                    )
                    _print({"ok": True, "result": result})
                elif args.command == "print-codex-hooks":
                    _print({"ok": True, "hooks": _codex_hooks_snippet()})
                elif args.command == "search":
                    results = svc.search_memories(
                        args.query,
                        session_id=args.session_id,
                        limit=args.limit,
                        include_historical=args.include_historical,
                    )
                    _print({"ok": True, "count": len(results), "results": results})
                elif args.command == "context-pack":
                    pack = svc.build_context_pack(
                        args.query,
                        session_id=args.session_id,
                        budget_tokens=args.budget,
                        limit=args.limit,
                        include_historical=args.include_historical,
                    )
                    if args.format == "text":
                        print(pack["text"])
                    else:
                        _print({"ok": True, "result": pack})
                elif args.command == "timeline":
                    events = svc.timeline(args.session_id, limit=args.limit)
                    _print({"ok": True, "count": len(events), "events": events})
                elif args.command == "export":
                    rows = svc.export_snapshot(args.out, session_id=args.session_id)
                    _print({"ok": True, "rows": rows, "out": str(args.out.resolve())})
                elif args.command == "import":
                    rows = svc.import_snapshot(args.file)
                    _print({"ok": True, "rows": rows, "source": str(args.file.resolve())})
                elif args.command == "session-summary":
                    result = svc.generate_session_summary(args.session_id)
                    _print({"ok": True, "result": result})
                elif args.command == "metrics":
                    _print({"ok": True, "result": svc.inspect_metrics()})
                elif args.command == "rebuild-indexes":
                    _print({"ok": True, "result": svc.rebuild_indexes(force_vectors=args.force_vectors)})
            finally:
                svc.close()
            return 0

        if args.command in {"graph-search", "graph-status", "graph-drain"}:
            settings = Settings.load()
            if args.offline:
                graph = GraphRagService(settings)
                try:
                    if args.command == "graph-search":
                        result = graph.graph_search(
                            query=args.query,
                            limit=args.limit,
                            include_raw=args.include_raw,
                            include_historical=args.include_historical,
                        )
                    elif args.command == "graph-drain":
                        result = graph.drain_evidence(limit=args.limit, session_id=args.session_id)
                    else:
                        result = graph.merge_status(session_id=args.session_id)
                    _print(result)
                finally:
                    graph.close()
            else:
                client_timeout = 300 if args.command == "graph-drain" else 60
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
                        result = client.post("/graph/drain", {"session_id": args.session_id, "limit": args.limit})
                    else:
                        result = client.get("/api/graph/status", {"session_id": args.session_id})
                except DaemonUnavailable as exc:
                    _print(
                        {
                            "ok": False,
                            "requires_daemon": True,
                            "error": str(exc),
                            "hint": "Start the daemon with: python -m agent_memory_orchestrator.daemon",
                        }
                    )
                    return 1
                _print(result)
            return 0

        if args.command == "debug":
            settings = Settings.load()
            if args.debug_command == "hooks":
                _print(debug_hooks(settings))
                return 0
            if args.debug_command == "qwen":
                _print(debug_qwen(settings, sample=args.sample))
                return 0
            if args.debug_command in {"drain", "retrieval", "graph"}:
                client = DaemonClient.from_settings(settings, timeout_seconds=30)
                try:
                    if args.debug_command == "drain":
                        _print(client.get("/api/debug/drain", {"session_id": args.session_id}))
                    elif args.debug_command == "graph":
                        _print(client.get("/api/debug/graph", {"session_id": args.session_id}))
                    else:
                        _print(client.post("/graph/search", {"query": args.query, "limit": args.limit, "debug": True}))
                except DaemonUnavailable as exc:
                    _print({"ok": False, "requires_daemon": True, "error": str(exc)})
                    return 1
                return 0

        settings = Settings.load()
        orch = OrchestratorService(settings)
        try:
            if args.command == "orchestrate-start":
                payload = orch.start(session_id=args.session_id, title=args.title)
            elif args.command == "orchestrate-submit":
                payload = orch.submit(
                    session_id=args.session_id,
                    agent=args.agent,
                    summary=args.summary,
                    confidence=args.confidence,
                    artifact_uri=args.artifact_uri,
                    blocking_issues=args.blocking_issue,
                )
            elif args.command == "orchestrate-status":
                payload = orch.status(session_id=args.session_id)
            elif args.command == "orchestrate-decision":
                payload = orch.user_decision(
                    session_id=args.session_id,
                    decision=args.decision,
                    notes=args.notes,
                    decided_by=args.decided_by,
                )
            else:
                parser.error(f"unknown command: {args.command}")
                return 2
            _print({"ok": True, "result": payload})
        finally:
            orch.close()
        return 0
    except (DaemonUnavailable, GraphBackendUnavailable, QwenUnavailable) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


def _codex_hooks_snippet() -> dict:
    command = "python -m agent_memory_orchestrator.hook --agent codex"
    return {
        "format": "toml",
        "snippet": "\n".join(
            [
                "[features]",
                "codex_hooks = true",
                "",
                "[[hooks.SessionStart]]",
                'matcher = "startup|resume|clear"',
                "[[hooks.SessionStart.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO starting graph capture"',
                "",
                "[[hooks.UserPromptSubmit]]",
                "[[hooks.UserPromptSubmit.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing prompt evidence"',
                "",
                "[[hooks.PostToolUse]]",
                'matcher = "*"',
                "[[hooks.PostToolUse.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing tool evidence"',
                "",
                "[[hooks.Stop]]",
                "[[hooks.Stop.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing session stop"',
            ]
        ),
    }


def _add_model_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=["cpu-light", "cpu-balanced", "gpu-quality"],
        default="cpu-balanced",
        help="Hardware-oriented model preset.",
    )
    parser.add_argument("--embedding-model", help="Override preset embedding model.")
    parser.add_argument("--reranker-model", help="Override preset reranker model.")
    parser.add_argument("--qwen-model", help="Override preset Ollama Qwen model.")


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _summarize_install_plan(plan: dict) -> dict:
    operations = []
    for op in plan["operations"]:
        path = Path(op["path"])
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        after = op["after"]
        safe_after = redact_secrets(after)[0]
        operations.append(
            {
                "target": op["target"],
                "path": op["path"],
                "description": op["description"],
                "exists": op["exists"],
                "changed": before != after,
                "after_preview": safe_after[:2000],
                "after_truncated": len(safe_after) > 2000,
            }
        )
    return {
        "target": plan["target"],
        "targets": plan["targets"],
        "user_home": plan["user_home"],
        "amo_home": plan["amo_home"],
        "models": plan["models"],
        "operations": operations,
        "notes": plan["notes"],
    }


def _rebuild_clean_db(settings: Settings, out_path: Path, codex_root: Path, limit: int, force: bool) -> dict:
    target = out_path.resolve()
    if target.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing DB without --force: {target}")
    if force:
        for path in (target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
            if path.exists():
                path.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean_settings = replace(settings, db_path=target)
    svc = MemoryService(clean_settings)
    try:
        svc.init_db()
        result = svc.import_codex_sessions(codex_root, limit=limit)
        indexes = svc.rebuild_indexes(force_vectors=False)
        return {
            "out": str(target),
            "codex_root": str(codex_root.resolve()),
            "import": result,
            "indexes": indexes,
        }
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
