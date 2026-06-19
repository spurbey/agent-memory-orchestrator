"""AMO proxy HTTP server.

Owns: HTTP transport only.

Routes:
  POST /v1/responses  — passthrough; optionally mutates when AMO_PROXY_MUTATE=1
  GET  /health        — status including mutation readiness

Mutation runs only when AMO_PROXY_MUTATE=1. All mutation failures are
fail-open: the original request bytes are forwarded unchanged.

WebSocket (/v1/responses WS) is not implemented.
Codex subscription mode may require it. When added, use a real
websockets client/server relay, not httpx streaming.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from .raw_store import ProxyRawOutputStore
from .ranker import ProxyRankToolHitsAdapter
from .tool_outputs import mutate_ranked_tool_outputs

# Headers that must not be forwarded upstream (hop-by-hop + internal)
_HOP_BY_HOP = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "accept-encoding",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "upgrade",
    }
)

_DEFAULT_UPSTREAM = "https://api.openai.com/v1"

# Module-level client injected at startup or by tests
_http_client: httpx.AsyncClient | None = None


def _normalize_base_url(raw: str) -> str:
    """Normalize upstream base URL so it always ends with /v1.

    https://api.openai.com/v1          -> https://api.openai.com/v1
    https://api.openai.com/v1/         -> https://api.openai.com/v1
    https://api.openai.com/v1/responses-> https://api.openai.com/v1
    https://api.openai.com             -> https://api.openai.com/v1
    http://127.0.0.1:8766/v1           -> http://127.0.0.1:8766/v1
    """
    url = raw.rstrip("/")
    v1_pos = url.find("/v1")
    if v1_pos != -1:
        return url[: v1_pos + 3]
    return url + "/v1"


def _try_mutate(body: bytes, raw_store: ProxyRawOutputStore, ranker: ProxyRankToolHitsAdapter) -> bytes:
    """Attempt payload mutation. Returns original bytes on any failure."""
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return body
    except Exception:
        return body

    try:
        result = mutate_ranked_tool_outputs(
            payload,
            raw_store=raw_store.save,
            ranker=ranker.rank,
        )
    except Exception:
        return body

    if not result.modified:
        return body

    try:
        return json.dumps(result.payload).encode("utf-8")
    except Exception:
        return body


def create_app(
    *,
    upstream_base_url: str | None = None,
    raw_store: ProxyRawOutputStore | None = None,
    ranker: ProxyRankToolHitsAdapter | None = None,
) -> FastAPI:
    upstream = _normalize_base_url(
        upstream_base_url
        or os.environ.get("AMO_OPENAI_UPSTREAM", _DEFAULT_UPSTREAM)
    )
    mutation_requested = os.environ.get("AMO_PROXY_MUTATE", "").strip() == "1"
    repo_id = os.environ.get("AMO_PROXY_REPO_ID", "").strip()

    # Build mutation dependencies lazily if not injected
    _raw_store = raw_store
    _ranker = ranker

    if mutation_requested and _raw_store is None:
        _raw_store = ProxyRawOutputStore()
    if mutation_requested and _ranker is None:
        from .ranker import make_ranker_from_env
        _ranker = make_ranker_from_env()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        global _http_client
        _http_client = httpx.AsyncClient(timeout=120.0)
        try:
            yield
        finally:
            await _http_client.aclose()
            _http_client = None

    app = FastAPI(title="AMO Proxy", lifespan=lifespan)
    app.state.upstream = upstream
    app.state.mutation_requested = mutation_requested

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "proxy": "amo",
            "upstream": upstream,
            "mutation_requested": mutation_requested,
            "mutation_supported": True,
            "mutation_mode": "rank_tool_hits",
            "repo_id_configured": bool(repo_id),
        }

    @app.post("/v1/responses")
    async def proxy_responses(request: Request) -> Response:
        body = await request.body()

        # Optionally mutate before forwarding
        outbound_body = body
        if mutation_requested and _raw_store is not None and _ranker is not None:
            outbound_body = _try_mutate(body, _raw_store, _ranker)

        outbound_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        outbound_headers["accept-encoding"] = "identity"
        if outbound_body is not body:
            outbound_headers["content-type"] = "application/json"

        assert _http_client is not None, "httpx client not initialised"

        upstream_request = _http_client.build_request(
            "POST",
            f"{upstream}/responses",
            content=outbound_body,
            headers=outbound_headers,
        )
        upstream_response = await _http_client.send(upstream_request, stream=True)

        response_headers = {
            k: v
            for k, v in upstream_response.headers.items()
            if k.lower() not in _HOP_BY_HOP | {"content-encoding"}
        }

        return StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            background=None,
        )

    return app
