"""Web asset runtime boundary.

The implementation remains in the daemon asset module for this tranche so HTTP
serving behavior does not change. New runtime code should import this boundary.
"""

from __future__ import annotations

from ..daemon.web_assets import graph_workbench_html
from ..daemon.web_assets import load_web_asset
from ..daemon.web_assets import session_cockpit_html
from ..daemon.web_assets import web_asset_bytes

__all__ = ["graph_workbench_html", "load_web_asset", "session_cockpit_html", "web_asset_bytes"]
