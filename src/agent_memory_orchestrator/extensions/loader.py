from __future__ import annotations

from pathlib import Path


LOCAL_EXTENSION_DIRS = (
    ".local-extensions",
    ".private-extensions",
    "src/agent_memory_orchestrator/extensions/local",
    "src/agent_memory_orchestrator/extensions/private",
    "src/agent_memory_orchestrator/extensions/experimental",
)


def extension_search_roots(repo_root: Path) -> tuple[Path, ...]:
    root = repo_root.resolve()
    return tuple(root / relative for relative in LOCAL_EXTENSION_DIRS)


def discover_extension_paths(repo_root: Path) -> list[Path]:
    """Return local extension directories without importing or executing code."""

    paths: list[Path] = []
    for root in extension_search_roots(repo_root):
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if child.is_dir() and not child.name.startswith((".", "__")):
                paths.append(child)
    return paths


__all__ = ["LOCAL_EXTENSION_DIRS", "discover_extension_paths", "extension_search_roots"]
