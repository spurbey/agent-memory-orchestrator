"""CLI command group handling for local model operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....llm.models import download_models, list_model_presets, model_status, preflight_models

MODEL_COMMANDS = ("models",)
MODEL_SUBCOMMANDS = ("list", "status", "download", "preflight")


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


__all__ = ["MODEL_COMMANDS", "MODEL_SUBCOMMANDS", "handle_models_command"]
