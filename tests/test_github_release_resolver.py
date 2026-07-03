from __future__ import annotations

import json

import pytest

from agent_memory_orchestrator.infrastructure import github_releases
from agent_memory_orchestrator.infrastructure.github_releases import component_manifest_url


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_component_manifest_url_uses_matching_latest_release_family(monkeypatch: pytest.MonkeyPatch) -> None:
    releases = [
        {
            "tag_name": "peer-netd-v0.1.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "peer-netd-manifest.json", "browser_download_url": "https://example.test/peer.json"}],
        },
        {
            "tag_name": "antelligent-v0.1.1",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "antelligent-manifest.json", "browser_download_url": "https://example.test/ant.json"}],
        },
    ]
    monkeypatch.setattr(github_releases.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(releases))

    url = component_manifest_url(tag_prefix="antelligent-v", manifest_asset="antelligent-manifest.json")

    assert url == "https://example.test/ant.json"


def test_component_manifest_url_explicit_version_uses_component_tag_without_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("explicit component versions must not call the releases API")

    monkeypatch.setattr(github_releases.urllib.request, "urlopen", fail_urlopen)

    url = component_manifest_url(
        tag_prefix="antelligent-v",
        manifest_asset="antelligent-manifest.json",
        version="0.1.1",
    )

    assert url == "https://github.com/spurbey/agent-memory-orchestrator/releases/download/antelligent-v0.1.1/antelligent-manifest.json"

