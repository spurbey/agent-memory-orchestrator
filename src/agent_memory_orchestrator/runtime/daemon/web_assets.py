from __future__ import annotations

from pathlib import Path

_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


def load_web_asset(name: str) -> str:
    return (_WEB_ROOT / name).read_text(encoding="utf-8")


def web_asset_bytes(name: str) -> tuple[bytes, str]:
    path = (_WEB_ROOT / name).resolve()
    root = _WEB_ROOT.resolve()
    if root not in path.parents and path != root:
        raise ValueError("invalid web asset path")
    data = path.read_bytes()
    suffix = path.suffix.lower()
    content_type = {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }.get(suffix, "application/octet-stream")
    return data, content_type


def session_cockpit_html() -> str:
    return load_web_asset("index.html")


def graph_workbench_html() -> str:
    return load_web_asset("graph.html")
