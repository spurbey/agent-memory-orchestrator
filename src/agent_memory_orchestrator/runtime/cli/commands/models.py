"""CLI command groups for local model operations."""

from __future__ import annotations

MODEL_COMMANDS = ("models",)
MODEL_SUBCOMMANDS = ("list", "status", "download", "preflight")

__all__ = ["MODEL_COMMANDS", "MODEL_SUBCOMMANDS"]
