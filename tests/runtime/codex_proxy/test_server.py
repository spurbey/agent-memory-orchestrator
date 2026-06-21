"""Tests for runtime/codex_proxy/server.py

Uses httpx.MockTransport to intercept upstream calls.
Requires only the proxy extra: fastapi, httpx, uvicorn.
No respx or other third-party mock library needed.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi", reason="install proxy extra: pip install agent-memory-orchestrator[proxy]")
pytest.importorskip("httpx", reason="install proxy extra: pip install agent-memory-orchestrator[proxy]")

import httpx
from httpx import AsyncClient, Request, Response
from httpx import ASGITransport

from agent_memory_orchestrator.runtime.codex_proxy.server import (
    _HOP_BY_HOP,
    _normalize_base_url,
    create_app,
)

UPSTREAM = "https://api.openai.com/v1"


# ---------------------------------------------------------------------------
# MockTransport: captures outbound request, returns canned response
# ---------------------------------------------------------------------------

class _CapturingTransport(httpx.AsyncBaseTransport):
    """Records the last request sent through it; returns a fixed response."""

    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.last_request: Request | None = None
        self._status = status
        self._body = body

    async def handle_async_request(self, request: Request) -> Response:
        self.last_request = request
        # Use a stream so the server's aiter_raw() works correctly
        return Response(
            self._status,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(self._body),
        )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def transport():
    return _CapturingTransport()


@pytest.fixture
def app(transport, monkeypatch):
    monkeypatch.delenv("AMO_PROXY_MUTATE", raising=False)
    _app = create_app(upstream_base_url=UPSTREAM)
    # Patch the shared client with one backed by our mock transport
    import agent_memory_orchestrator.runtime.codex_proxy.server as _srv
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    return _app


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_returns_ok(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["proxy"] == "amo"
    assert body["upstream"] == UPSTREAM


@pytest.mark.anyio
async def test_health_mutation_supported_false(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    body = resp.json()
    assert body["mutation_supported"] is True  # wired in this slice
    assert body["mutation_mode"] == "rank_tool_hits"


@pytest.mark.anyio
async def test_health_mutation_requested_false_by_default(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.json()["mutation_requested"] is False


@pytest.mark.anyio
async def test_health_mutation_requested_true_when_env_set(transport, monkeypatch):
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    _app = create_app(upstream_base_url=UPSTREAM)
    import agent_memory_orchestrator.runtime.codex_proxy.server as _srv
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
        resp = await c.get("/health")
    body = resp.json()
    assert body["mutation_requested"] is True
    assert body["mutation_supported"] is True


# ---------------------------------------------------------------------------
# POST /v1/responses — forwarding
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_responses_forwards_body(app, transport):
    payload = {"model": "gpt-4o", "input": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/v1/responses",
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
    assert transport.last_request is not None
    assert json.loads(transport.last_request.content) == payload


@pytest.mark.anyio
async def test_responses_forwards_authorization_header(app, transport):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/v1/responses",
            content=b"{}",
            headers={
                "content-type": "application/json",
                "authorization": "Bearer sk-test-token",
            },
        )
    assert transport.last_request.headers["authorization"] == "Bearer sk-test-token"


@pytest.mark.anyio
async def test_responses_strips_hop_by_hop_headers(app, transport):
    extra = {h: "strip-me" for h in _HOP_BY_HOP}
    extra["content-type"] = "application/json"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=b"{}", headers=extra)
    sent = transport.last_request.headers
    for hop in _HOP_BY_HOP:
        assert sent.get(hop) != "strip-me", (
            f"hop-by-hop header '{hop}' was forwarded but should have been stripped"
        )


@pytest.mark.anyio
async def test_responses_upstream_url_correct(app, transport):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/v1/responses",
            content=b"{}",
            headers={"content-type": "application/json"},
        )
    called = str(transport.last_request.url)
    assert called == f"{UPSTREAM}/responses"
    assert "/v1/v1/" not in called


@pytest.mark.anyio
async def test_responses_streams_upstream_status_code(app, transport):
    transport._status = 429
    transport._body = b'{"error":"rate_limited"}'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/responses",
            content=b"{}",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# _normalize_base_url — unit tests (no network)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://api.openai.com/v1",           "https://api.openai.com/v1"),
    ("https://api.openai.com/v1/",          "https://api.openai.com/v1"),
    ("https://api.openai.com/v1/responses", "https://api.openai.com/v1"),
    ("https://api.openai.com",              "https://api.openai.com/v1"),
    ("http://127.0.0.1:8766/v1",            "http://127.0.0.1:8766/v1"),
    ("http://127.0.0.1:8766/v1/",           "http://127.0.0.1:8766/v1"),
])
def test_normalize_base_url(raw, expected):
    assert _normalize_base_url(raw) == expected


# ---------------------------------------------------------------------------
# Mutation is called when AMO_PROXY_MUTATE=1
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_mutation_called_when_flag_set(transport, monkeypatch):
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    called = []
    import agent_memory_orchestrator.runtime.codex_proxy.server as _srv_mod
    original = _srv_mod._try_mutate

    def _spy(*a, **kw):
        called.append(True)
        return original(*a, **kw)

    monkeypatch.setattr(_srv_mod, "_try_mutate", _spy)
    monkeypatch.setattr(_srv_mod, "_http_client", httpx.AsyncClient(transport=transport))

    _app = create_app(upstream_base_url=UPSTREAM)
    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
        await c.post(
            "/v1/responses",
            content=b'{"input":[]}',
            headers={"content-type": "application/json"},
        )
    assert called, "mutate_ranked_tool_outputs must be called when AMO_PROXY_MUTATE=1"
