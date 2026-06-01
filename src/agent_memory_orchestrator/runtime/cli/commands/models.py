"""CLI command group handling for local model operations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....infrastructure.llm import download_models, list_model_presets, model_status, preflight_models
from .install import add_model_selection_args

MODEL_COMMANDS = ("models",)
MODEL_SUBCOMMANDS = ("list", "status", "download", "preflight")


def add_models_subcommands(sub: Any) -> None:
    models = sub.add_parser("models", help="Manage local embedding/reranker models")
    model_sub = models.add_subparsers(dest="models_command", required=True)
    model_sub.add_parser("list", help="List hardware-aware model presets")
    model_status_cmd = model_sub.add_parser("status", help="Check whether selected models are cached locally")
    add_model_selection_args(model_status_cmd)
    model_status_cmd.add_argument("--load-check", action="store_true", help="Also try loading models with local_files_only")
    model_download = model_sub.add_parser("download", help="Intentionally download/cache selected models once")
    add_model_selection_args(model_download)
    model_download.add_argument("--cache-dir", type=Path)
    model_preflight = model_sub.add_parser("preflight", help="Require selected models to load from local cache")
    add_model_selection_args(model_preflight)


def handle_models_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run local model-management commands."""
    if args.command != "models":
        return None

    if args.models_command == "list":
        emit({"ok": True, "presets": list_model_presets()})
    elif args.models_command == "status":
        emit(
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
        emit(
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
        emit({"ok": result["ok"], "result": result})
        return 0 if result["ok"] else 1
    return 0


__all__ = ["MODEL_COMMANDS", "MODEL_SUBCOMMANDS", "add_models_subcommands", "handle_models_command"]
