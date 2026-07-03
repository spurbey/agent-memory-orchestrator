from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.parse import quote

DEFAULT_GITHUB_REPOSITORY = "spurbey/agent-memory-orchestrator"


def component_manifest_url(
    *,
    tag_prefix: str,
    manifest_asset: str,
    version: str = "latest",
    repository: str = DEFAULT_GITHUB_REPOSITORY,
    include_prerelease: bool = False,
) -> str:
    """Resolve a component-scoped manifest URL for repos with multiple release families."""

    resolved_version = (version or "latest").strip()
    if resolved_version and resolved_version != "latest":
        tag = resolved_version if resolved_version.startswith(tag_prefix) else f"{tag_prefix}{resolved_version}"
        return _download_url(repository=repository, tag=tag, asset=manifest_asset)

    releases = _github_releases(repository)
    for release in releases:
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prerelease:
            continue
        tag = str(release.get("tag_name") or "")
        if not tag.startswith(tag_prefix):
            continue
        for asset in release.get("assets", []):
            if str(asset.get("name") or "") == manifest_asset:
                url = str(asset.get("browser_download_url") or "")
                if url:
                    return url
        raise FileNotFoundError(f"release {tag} is missing {manifest_asset}")
    raise FileNotFoundError(f"no GitHub release found with tag prefix {tag_prefix!r}")


def _github_releases(repository: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "amo-installer"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub HTTPS API.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("GitHub releases response must be a JSON list")
    return [item for item in payload if isinstance(item, dict)]


def _download_url(*, repository: str, tag: str, asset: str) -> str:
    return f"https://github.com/{repository}/releases/download/{quote(tag, safe='')}/{quote(asset, safe='')}"


__all__ = ["DEFAULT_GITHUB_REPOSITORY", "component_manifest_url"]
