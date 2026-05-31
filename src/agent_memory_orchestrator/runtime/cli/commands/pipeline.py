"""CLI command groups for production pipeline operations."""

from __future__ import annotations

PIPELINE_COMMANDS = (
    "init-production",
    "reset-production",
    "adopt-production",
    "production",
)

PRODUCTION_SUBCOMMANDS = (
    "export-fixture",
    "semantic-eval",
    "eval",
    "merge-plan",
    "merge-apply",
)

__all__ = ["PIPELINE_COMMANDS", "PRODUCTION_SUBCOMMANDS"]
