"""Compatibility wrapper for the runtime daemon client."""

from __future__ import annotations

from .runtime.daemon.client import DaemonClient, DaemonUnavailable

__all__ = ["DaemonClient", "DaemonUnavailable"]
