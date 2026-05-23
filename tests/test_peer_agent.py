from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.mcp.tools import MCP_MEMORY_TOOL_CONTRACTS, MemoryMcpToolService
from agent_memory_orchestrator.peer.agent import PeerAgentService
from agent_memory_orchestrator.peer.agent.schemas import CONTEXT_REQUEST, CONTEXT_RESPONSE, RESPONSE_RETRIEVAL_BUNDLE
from agent_memory_orchestrator.peer.models import PeerNode
from agent_memory_orchestrator.peer.service import PeerService
from agent_memory_orchestrator.peer.store import PeerStore


def test_peer_agent_local_high_quality_returns_local_only_without_room(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store), graph=FakeGraph(good_retrieval()))

    result = svc.ask(query="what was the local first architecture decision", timeout_seconds=0)

    assert result["mode"] == "local_only"
    assert result["room_id"] == ""
    assert store.list_rooms() == []


def test_peer_agent_low_quality_creates_room_and_context_request(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer", capabilities=("graph_retrieval",)))
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(low_retrieval()),
    )

    result = svc.ask(query="ask peers about missing context", timeout_seconds=0)

    assert result["mode"] == "timed_out"
    room_id = result["room_id"]
    state = json.loads((settings.home / ".peer" / "rooms" / room_id / "agent_state.json").read_text(encoding="utf-8"))
    assert state["original_query"] == "ask peers about missing context"
    context_messages = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_REQUEST]
    assert len(context_messages) == 1
    metadata = context_messages[0]["payload"]["metadata"]
    assert metadata["request_id"].startswith("req_")
    assert metadata["audience"] == "peer"
    assert metadata["target_peer_id"] == "poco-amo"
    assert metadata["raw_evidence_requested"] is False


def test_peer_agent_watch_returns_llm_answer_when_peer_ollama_available(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    llm = FakeLlm(peer_answer={"answer": "Peer found the local-first decision.", "confidence": 0.91, "answer_grade": True, "gaps": []})
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=llm,
    )

    result = svc.watch_once()

    assert result["processed_count"] == 1
    responses = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE]
    assert len(responses) == 1
    assert responses[0]["payload"]["metadata"]["mode"] == "llm_answer"
    assert responses[0]["payload"]["metadata"]["answer_grade"] is True


def test_peer_agent_watch_returns_retrieval_bundle_without_peer_llm(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    svc.watch_once()

    responses = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE]
    assert responses[0]["payload"]["metadata"]["mode"] == RESPONSE_RETRIEVAL_BUNDLE
    bundle = responses[0]["payload"]["metadata"]["retrieval_bundle"]
    assert bundle["answer"]["text"]
    assert bundle["support"]
    assert "retrieval" not in bundle


def test_peer_agent_duplicate_request_is_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    svc.watch_once()
    svc.watch_once()

    responses = [item for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE]
    assert len(responses) == 1


def test_peer_agent_retries_failed_response_delivery(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient(send_ok_sequence=[False, True])
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    first = svc.watch_once()
    second = svc.watch_once()

    responses = [item for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE]
    assert len(responses) == 2
    assert first["processed"][0]["ok"] is False
    assert second["processed"][0]["ok"] is True
    room_id = store.list_rooms()[0]["room_id"]
    transcript = store.read_messages(room_id)
    assert [item["type"] for item in transcript].count(CONTEXT_RESPONSE) == 1
    state = json.loads((settings.home / ".peer" / "rooms" / room_id / "agent_state.json").read_text(encoding="utf-8"))
    assert state["response_attempts"]["req_1"]["attempt_count"] == 2
    assert "req_1" in state["sent_response_for_request_ids"]


def test_peer_agent_skips_stale_manual_context_requests(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo", schema_version=0)
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    result = svc.watch_once()

    assert netd.sent == []
    assert result["processed"][0]["skipped"] is True
    assert result["processed"][0]["reason"] == "invalid_schema_version"


def test_peer_agent_skips_schema_valid_request_without_transport_auth(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo", include_transport_auth=False)
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    result = svc.watch_once()

    assert netd.sent == []
    assert result["processed"][0]["skipped"] is True
    assert result["processed"][0]["reason"] == "missing_verified_transport"


def test_peer_agent_skips_context_request_when_peer_is_not_tagged(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo", targeted=False)
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    result = svc.watch_once()

    assert netd.sent == []
    assert result["processed"][0]["skipped"] is True
    assert result["processed"][0]["reason"] == "request_not_tagged_for_peer"


def test_peer_agent_redacts_local_refs_when_citation_sharing_disabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    store.save_config(replace(store.load_config(), share_citations=False))
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(local_ref_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )

    svc.watch_once()

    response = next(item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE)
    support = response["payload"]["metadata"]["support"]
    bundle = response["payload"]["metadata"]["retrieval_bundle"]
    content = response["payload"]["content"]
    assert support
    assert support[0]["local_ref"] == {}
    assert "retrieval" not in bundle
    for leaked in ("E00028", "WP0001", "decision:local-first", "raw_test"):
        assert leaked not in bundle["answer"]["text"]
        assert leaked not in content


def test_peer_agent_disabled_config_blocks_automation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, peer_agent_enabled=False)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store), graph=FakeGraph(good_retrieval()))

    with pytest.raises(RuntimeError, match="peer-agent is disabled"):
        svc.ask(query="what was local-first", timeout_seconds=0)


def test_initiator_synthesizes_from_peer_response_with_own_llm(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer"))
    room = store.create_room(topic="what was local-first", participants=["poco-amo"])
    store.append_message(room["room_id"], strong_peer_response(room["room_id"], mode="llm_answer"))
    llm = FakeLlm(final_answer={"answer": "Synthesized by initiator.", "confidence": 0.93, "mode": "peer_assisted", "gaps": []})
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store, netd_client=FakeNetdClient()), llm=llm)
    svc.state.save(
        room["room_id"],
        {
            "agent_managed": True,
            "schema_version": 1,
            "status": "open",
            "original_query": "what was local-first",
            "local_retrieval": compact_local_answer("local fallback"),
            "deadline_at": "2999-01-01T00:00:00+00:00",
        },
    )

    result = svc.watch_once()

    assert any(item.get("finalized") for item in result["processed"])
    state = json.loads((settings.home / ".peer" / "rooms" / room["room_id"] / "agent_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "finalized"
    assert state["final"]["answer"] == "Synthesized by initiator."
    assert llm.final_calls == 1


def test_initiator_without_llm_returns_retrieval_only_for_bundle(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    room = store.create_room(topic="what was local-first", participants=["poco-amo"])
    store.append_message(room["room_id"], strong_peer_response(room["room_id"], mode=RESPONSE_RETRIEVAL_BUNDLE))
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store), llm=FakeLlm(fail_final=True))
    svc.state.save(
        room["room_id"],
        {
            "agent_managed": True,
            "schema_version": 1,
            "status": "open",
            "original_query": "what was local-first",
            "local_retrieval": compact_local_answer(""),
            "deadline_at": "2999-01-01T00:00:00+00:00",
        },
    )

    svc.watch_once()

    state = json.loads((settings.home / ".peer" / "rooms" / room["room_id"] / "agent_state.json").read_text(encoding="utf-8"))
    assert state["final"]["mode"] == "retrieval_only"
    assert "Peer response text" in state["final"]["answer"]


def test_peer_agent_does_not_write_initiator_api_key_to_room_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INITIATOR_API_KEY", "sk-test-secret-never-write")
    settings = make_settings(
        tmp_path,
        peer_agent_api_provider="openai_compatible",
        peer_agent_api_base_url="http://127.0.0.1:1/v1",
        peer_agent_api_model="test-model",
        peer_agent_api_key_env="INITIATOR_API_KEY",
    )
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer", capabilities=("graph_retrieval",)))
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=FakeNetdClient()),
        graph=FakeGraph(low_retrieval()),
        llm=FakeLlm(fail_final=True),
    )

    result = svc.ask(query="ask peers without leaking key", timeout_seconds=0)

    room_dir = settings.home / ".peer" / "rooms" / result["room_id"]
    room_text = "\n".join(path.read_text(encoding="utf-8") for path in room_dir.glob("*") if path.is_file())
    assert "sk-test-secret-never-write" not in room_text
    assert "INITIATOR_API_KEY" not in room_text


def test_peer_agent_mcp_contracts_are_registered(tmp_path: Path) -> None:
    service = MemoryMcpToolService(make_settings(tmp_path), peer_agent=FakePeerAgent())

    contracts = service.tool_contracts()
    result = service.peer_memory_ask(query="hello", timeout_seconds=0)

    assert "peer_memory_ask" in MCP_MEMORY_TOOL_CONTRACTS
    assert "peer_room_messages" in contracts["tools"]
    assert result["mode"] == "retrieval_only"


def peer_room_with_request(
    tmp_path: Path,
    *,
    local_node: str,
    schema_version: int = 1,
    agent_schema_version: int = 1,
    include_transport_auth: bool = True,
    targeted: bool = True,
) -> PeerStore:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id=local_node)
    store.add_peer(PeerNode(node_id="zenbook-amo", peer_id="12D3KooWInitiator", trust="trusted"))
    room = store.create_room(
        topic="what was the local first architecture decision",
        participants=["zenbook-amo", local_node],
        initiator_node_id="zenbook-amo",
    )
    metadata = {
        "schema_version": schema_version,
        "agent_room_schema_version": agent_schema_version,
        "request_id": "req_1",
        "query": "what was the local first architecture decision",
        "min_confidence": 0.72,
        "deadline_at": "2999-01-01T00:00:00+00:00",
        "raw_evidence_requested": False,
    }
    if include_transport_auth:
        metadata["transport_auth"] = {
            "auth": "netd:none",
            "authenticated": False,
            "remote_peer_id": "12D3KooWInitiator",
        }
    store.append_message(
        room["room_id"],
        {
            "message_id": "msg_request_1",
            "type": CONTEXT_REQUEST,
            "from": "zenbook-amo",
            "from_node_id": "zenbook-amo",
            "to": [local_node] if targeted else [],
            "to_node_ids": [local_node] if targeted else [],
            "content": "what was the local first architecture decision",
            "citations": [],
            "metadata": metadata,
        },
    )
    return store


def compact_local_answer(text: str) -> dict[str, Any]:
    return {"answer": {"text": text}, "retrieval": {"hits": []}}


def strong_peer_response(room_id: str, *, mode: str) -> dict[str, Any]:
    support = [
        {
            "source_peer": "poco-amo",
            "visibility": "summary_only",
            "local_ref": {"packet_id": "WP0001", "evidence_id": "E00028", "node_id": "decision:local-first"},
            "shared_ref": {"repo": "agent-memory-orchestrator", "commit": "61b51d9", "path": "docs/ARCHITECTURE.md", "symbol": ""},
            "claim": "AMO keeps memory and orchestration local-first.",
            "claim_sha256": "abc",
        }
    ]
    return {
        "message_id": "msg_response_1",
        "type": CONTEXT_RESPONSE,
        "room_id": room_id,
        "from": "poco-amo",
        "from_node_id": "poco-amo",
        "to": ["zenbook-amo"],
        "to_node_ids": ["zenbook-amo"],
        "content": "Peer response text with a portable commit citation.",
        "confidence": 0.91,
        "citations": ["commit:61b51d9"],
        "metadata": {
            "request_id": "req_1",
            "mode": mode,
            "answer_grade": True,
            "quality": {"answer_grade": True, "confidence": 0.91},
            "support": support,
            "retrieval_bundle": {"answer": {"text": "Peer response text with a portable commit citation."}},
        },
    }


def good_retrieval() -> dict[str, Any]:
    return {
        "ok": True,
        "retrieval": {
            "intent": "decision_lookup",
            "vector_status": "completed",
            "hits": [
                {
                    "score": 4.5,
                    "document": {
                        "doc_id": "doc_1",
                        "doc_type": "decision",
                        "node_kind": "Decision",
                        "graph_node_id": "decision:local-first",
                        "packet_id": "WP0001",
                        "commit_sha": "61b51d9",
                        "title": "Local first architecture decision",
                        "body": "local first architecture decision keeps memory orchestration and session storage local",
                    },
                    "graph_node": {"id": "decision:local-first", "kind": "Decision", "summary": "Local first architecture decision"},
                    "neighbors": [],
                }
            ],
        },
        "answer": {
            "text": "AMO indexed graph answer:\n1. Local-first architecture decision.",
            "node_ids": ["decision:local-first"],
            "citations": [
                {
                    "graph_node_id": "decision:local-first",
                    "packet_id": "WP0001",
                    "commit_sha": "61b51d9",
                    "evidence_ids": ["E00028"],
                    "code_nodes": ["docs/ARCHITECTURE.md"],
                    "trace": {"narrative": [{"summary": "Local-first architecture decision"}]},
                }
            ],
        },
    }


def local_ref_retrieval() -> dict[str, Any]:
    result = good_retrieval()
    result["answer"]["text"] = "AMO indexed graph answer: E00028 WP0001 decision:local-first raw_test should be hidden."
    return result


def low_retrieval() -> dict[str, Any]:
    return {
        "ok": True,
        "retrieval": {"intent": "general", "vector_status": "completed", "hits": []},
        "answer": {"text": "No indexed graph evidence matched the query.", "citations": [], "node_ids": []},
    }


class FakeGraph:
    def __init__(self, *results: dict[str, Any]) -> None:
        self.results = list(results)

    def retrieve_indexed_graph(self, **kwargs: Any) -> dict[str, Any]:
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]

    def close(self) -> None:
        return None


class FakeLlm:
    def __init__(
        self,
        *,
        peer_answer: dict[str, Any] | None = None,
        final_answer: dict[str, Any] | None = None,
        fail_peer: bool = False,
        fail_final: bool = False,
    ) -> None:
        self.peer_answer = peer_answer or {"answer": "peer answer", "confidence": 0.9, "answer_grade": True, "gaps": []}
        self.final_answer = final_answer or {"answer": "final answer", "confidence": 0.9, "mode": "peer_assisted", "gaps": []}
        self.fail_peer = fail_peer
        self.fail_final = fail_final
        self.final_calls = 0

    def generate_peer_answer(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_peer:
            raise RuntimeError("peer llm unavailable")
        return self.peer_answer

    def synthesize_final(self, **kwargs: Any) -> dict[str, Any]:
        self.final_calls += 1
        if self.fail_final:
            raise RuntimeError("final llm unavailable")
        return self.final_answer

    def summarize_room(self, **kwargs: Any) -> dict[str, Any]:
        return {"summary_md": "# Rolling Summary\n\n## Current Understanding\n\n- Test summary."}


class FakePeerAgent:
    def ask(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "mode": "retrieval_only", "answer": "fake", "room_id": "", "local_quality": {}, "peer_responses": [], "citations": [], "timing": {}}

    def status(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room_id": room_id}

    def context(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room_id": room_id, "context": {}}

    def messages(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room_id": room_id, "messages": []}


class FakeNetdClient:
    def __init__(self, *, send_ok_sequence: list[bool] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.send_ok_sequence = list(send_ok_sequence or [])

    def messages(self) -> list[dict[str, Any]]:
        return []

    def send_raw(self, to_peer_id: str, message: dict[str, Any]) -> dict[str, Any]:
        self.sent.append({"to_peer_id": to_peer_id, "message": message})
        ok = self.send_ok_sequence.pop(0) if self.send_ok_sequence else True
        return {"ok": ok, "error": "" if ok else "simulated send failure"}

    def connect(self, addr: str) -> dict[str, Any]:
        return {"ok": True, "addr": addr}

    def rendezvous_discover(self, addr: str, namespace: str, connect: bool = True) -> list[dict[str, Any]]:
        return []


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    payload: dict[str, Any] = {
        "home": tmp_path,
        "db_path": tmp_path / "memory.db",
        "export_dir": tmp_path / "exports",
        "local_only": True,
        "mcp_transport": "stdio",
        "mcp_host": "127.0.0.1",
        "mcp_port": 8765,
        "embedding_dims": 64,
        "embedding_model": "hash-fallback",
        "reranker_model": "BAAI/bge-reranker-base",
        "vector_backend": "disabled",
        "approval_mode": "manual",
        "owner_user_id": "local",
        "workspace_id": "local",
        "project_id": "default",
        "visibility_scope": "private",
        "sensitivity_level": "normal",
        "consensus_threshold": 0.7,
        "max_review_rounds": 5,
        "graph_path": tmp_path / "graph" / "amo.kuzu",
        "evidence_dir": tmp_path / "evidence",
    }
    payload.update(overrides)
    return Settings(**payload)
