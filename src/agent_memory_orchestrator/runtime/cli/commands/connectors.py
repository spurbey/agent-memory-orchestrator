"""CLI command groups for connector operations."""

from __future__ import annotations

CONNECTOR_COMMANDS = ("slack",)

SLACK_SUBCOMMANDS = (
    "manifest",
    "setup-link",
    "bootstrap",
    "setup",
    "setup-wizard",
    "status",
    "ingest-event",
    "finalize-session",
    "run",
)

__all__ = ["CONNECTOR_COMMANDS", "SLACK_SUBCOMMANDS"]
