"""Tests for server.py mutation path (AMO_PROXY_MUTATE=1).

Uses httpx.ASGITransport + _CapturingTransport — no real network.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi", reason="install proxy extra")
pytest.importorskip("httpx", reason="install proxy extra")

import httpx
from httpx import ASGITransport, AsyncClient, Request, Response

import agent_memory_orchestrator.runtime.codex_proxy.server as _srv
from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult
from agent_memory_orchestrator.domain.semantic_harness.query_modes.rank_tool_hits import (
    RankedToolHit,
    RankedToolLine,
)
from agent_memory_orchestrator.runtime.codex_proxy.ranker import ProxyRankToolHitsAdapter
from agent_memory_orchestrator.runtime.codex_proxy.raw_store import ProxyRawOutputStore
from agent_memory_orchestrator.runtime.codex_proxy.server import create_app

UPSTREAM = "https://api.openai.com/v1"

# rg-style output that tool_outputs.py will detect as search output
RG_OUTPUT = "src/foo.py:10:def handle():\nsrc/bar.py:20:class Widget:"


class _CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.last_request: Request | None = None
        self._status = status
        self._body = body

    async def handle_async_request(self, request: Request) -> Response:
        self.last_request = request
        return Response(
            self._status,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(self._body),
        )


def _good_rank_result() -> RankToolHitsResult:
    return RankToolHitsResult(
        status="ready",
        ranked_hits=(
            RankedToolHit(
                path="src/foo.py",
                file_node_id="node_1",
                score=0.85,
                match_count=1,
                line_refs=(RankedToolLine(file_path="src/foo.py", line=10, text="def handle():"),),
                symbol_node_ids=(),
                semantic_similarity=0.7,
                semantic_doc_ids=(),
                reason_codes=("rg_match_strength:0.85",),
            ),
        ),
        query_text="",
        raw_ref="sha256:" + "a" * 64,
        embedding_backend="hash_token_char_cosine_v1",
        warnings=(),
    )


def _make_payload(output: str = RG_OUTPUT) -> dict:
    return {
        "model": "gpt-test",
        "input": [
            {
                "type": "local_shell_call_output",
                "call_id": "call_rg",
                "output": output,
            }
        ],
    }


@pytest.fixture
def transport():
    return _CapturingTransport()


@pytest.fixture
def raw_store(tmp_path):
    return ProxyRawOutputStore(root=tmp_path / "raw")


@pytest.fixture
def good_ranker():
    return ProxyRankToolHitsAdapter(rank_fn=lambda c: _good_rank_result())


@pytest.fixture
def no_hit_ranker():
    return ProxyRankToolHitsAdapter(rank_fn=lambda c: None)


def _app_with_mutation(transport, raw_store, ranker, monkeypatch):
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    app = create_app(upstream_base_url=UPSTREAM, raw_store=raw_store, ranker=ranker)
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    return app


def _app_no_mutation(transport, monkeypatch):
    monkeypatch.delenv("AMO_PROXY_MUTATE", raising=False)
    app = create_app(upstream_base_url=UPSTREAM)
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    return app


# ---------------------------------------------------------------------------
# Mutation disabled (AMO_PROXY_MUTATE unset)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_mutation_disabled_forwards_original_body(transport, monkeypatch):
    app = _app_no_mutation(transport, monkeypatch)
    payload = _make_payload()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    assert json.loads(transport.last_request.content) == payload


@pytest.mark.anyio
async def test_mutation_disabled_raw_store_not_called(transport, raw_store, monkeypatch):
    app = _app_no_mutation(transport, monkeypatch)
    payload = _make_payload()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    # raw store directory should be empty
    raw_dir = raw_store._root
    assert not raw_dir.exists() or list(raw_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Mutation enabled, rg output present → mutated body sent upstream
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_mutation_enabled_sends_ranked_output(transport, raw_store, good_ranker, monkeypatch):
    app = _app_with_mutation(transport, raw_store, good_ranker, monkeypatch)
    payload = _make_payload()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    sent = json.loads(transport.last_request.content)
    output = sent["input"][0]["output"]
    assert "AMO_RANKED_TOOL_HITS" in output
    assert "RAW_OUTPUT_REF" in output


@pytest.mark.anyio
async def test_mutation_enabled_raw_file_written(transport, raw_store, good_ranker, monkeypatch, tmp_path):
    app = _app_with_mutation(transport, raw_store, good_ranker, monkeypatch)
    payload = _make_payload()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    raw_files = list(raw_store._root.glob("*.txt"))
    assert len(raw_files) >= 1


# ---------------------------------------------------------------------------
# Fail-open cases: original body must reach upstream unchanged
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_invalid_json_forwarded_unchanged(transport, raw_store, good_ranker, monkeypatch):
    app = _app_with_mutation(transport, raw_store, good_ranker, monkeypatch)
    bad_bytes = b"not-json{"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=bad_bytes, headers={"content-type": "application/json"})
    assert transport.last_request.content == bad_bytes


@pytest.mark.anyio
async def test_non_rg_output_forwarded_unchanged(transport, raw_store, good_ranker, monkeypatch):
    app = _app_with_mutation(transport, raw_store, good_ranker, monkeypatch)
    payload = _make_payload(output="just some plain text output")
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=body, headers={"content-type": "application/json"})
    assert transport.last_request.content == body


@pytest.mark.anyio
async def test_raw_store_failure_forwards_original(transport, monkeypatch, tmp_path, good_ranker):
    # Make raw store root a file so mkdir fails
    bad_root = tmp_path / "file.txt"
    bad_root.write_text("x")
    bad_store = ProxyRawOutputStore(root=bad_root)
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    app = create_app(upstream_base_url=UPSTREAM, raw_store=bad_store, ranker=good_ranker)
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    payload = _make_payload()
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=body, headers={"content-type": "application/json"})
    assert transport.last_request.content == body


@pytest.mark.anyio
async def test_ranker_failure_forwards_original(transport, raw_store, monkeypatch):
    boom_ranker = ProxyRankToolHitsAdapter(rank_fn=lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    app = create_app(upstream_base_url=UPSTREAM, raw_store=raw_store, ranker=boom_ranker)
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    payload = _make_payload()
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=body, headers={"content-type": "application/json"})
    assert transport.last_request.content == body


@pytest.mark.anyio
async def test_no_hits_forwards_original(transport, raw_store, no_hit_ranker, monkeypatch):
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    app = create_app(upstream_base_url=UPSTREAM, raw_store=raw_store, ranker=no_hit_ranker)
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    payload = _make_payload()
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=body, headers={"content-type": "application/json"})
    assert transport.last_request.content == body


@pytest.mark.anyio
async def test_missing_repo_id_forwards_original(transport, raw_store, monkeypatch, tmp_path):
    no_repo_ranker = ProxyRankToolHitsAdapter(rank_fn=lambda c: None)
    monkeypatch.setenv("AMO_PROXY_MUTATE", "1")
    monkeypatch.delenv("AMO_PROXY_REPO_ID", raising=False)
    app = create_app(upstream_base_url=UPSTREAM, raw_store=raw_store, ranker=no_repo_ranker)
    monkeypatch.setattr(_srv, "_http_client", httpx.AsyncClient(transport=transport))
    payload = _make_payload()
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/v1/responses", content=body, headers={"content-type": "application/json"})
    assert transport.last_request.content == body


# ---------------------------------------------------------------------------
# Health endpoint with mutation wired
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_mutation_supported_true_when_wired(transport, raw_store, good_ranker, monkeypatch):
    app = _app_with_mutation(transport, raw_store, good_ranker, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    body = resp.json()
    assert body["mutation_supported"] is True
    assert body["mutation_requested"] is True
    assert body["mutation_mode"] == "rank_tool_hits"
