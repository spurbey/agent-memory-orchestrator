from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.runtime.mcp.tools import MCP_MEMORY_TOOL_CONTRACTS, MemoryMcpToolService
from agent_memory_orchestrator.peer.agent import PeerAgentService
from agent_memory_orchestrator.peer.agent.prompts import peer_answer_prompt
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


def test_peer_agent_ask_can_target_specific_peer(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeerA", capabilities=("graph_retrieval",)))
    store.add_peer(PeerNode(node_id="stale-vm", peer_id="12D3KooWPeerB", capabilities=("graph_retrieval",)))
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(low_retrieval()),
    )

    result = svc.ask(query="ask one peer only", peer_ids=["poco-amo"], timeout_seconds=0)

    assert result["room_id"]
    context_messages = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_REQUEST]
    assert len(context_messages) == 1
    assert context_messages[0]["to_node_id"] == "poco-amo"
    assert context_messages[0]["payload"]["metadata"]["target_peer_id"] == "poco-amo"


def test_peer_agent_ask_room_sends_schema_valid_followup(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer", capabilities=("graph_retrieval",)))
    room = store.create_room(topic="debug peer room", participants=["poco-amo"])
    netd = FakeNetdClient()
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store, netd_client=netd))

    result = svc.ask_room(
        room_id=room["room_id"],
        peer_ids=["poco-amo"],
        query="Can you answer a valid room follow-up?",
        timeout_seconds=60,
    )

    assert result["ok"] is True
    assert result["mode"] == "room_followup"
    context_messages = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_REQUEST]
    assert len(context_messages) == 1
    metadata = context_messages[0]["payload"]["metadata"]
    assert metadata["schema_version"] == 1
    assert metadata["agent_room_schema_version"] == 1
    assert metadata["request_id"].startswith("req_")
    assert metadata["logical_request_id"].startswith("q_")
    assert metadata["room_id"] == room["room_id"]
    assert metadata["target_peer_id"] == "poco-amo"
    assert metadata["query"] == "Can you answer a valid room follow-up?"


def test_peer_agent_ask_room_can_wait_for_target_response(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer", capabilities=("graph_retrieval",)))
    room = store.create_room(topic="debug peer room", participants=["poco-amo"])
    netd = FakeNetdClient()
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store, netd_client=netd))

    result = svc.ask_room(
        room_id=room["room_id"],
        peer_ids=["poco-amo"],
        query="Can you answer a valid room follow-up?",
        timeout_seconds=0.1,
        wait_for_response=True,
    )

    assert result["ok"] is True
    assert result["response_count"] == 0
    assert result["peer_responses"] == []
    assert result["timing"]["wait_ms"] >= 0


def test_peer_agent_ask_room_carries_initiator_summary_after_summary_exists(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer", capabilities=("graph_retrieval",)))
    room = store.create_room(topic="debug peer room", participants=["poco-amo"])
    netd = FakeNetdClient()
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store, netd_client=netd))
    PeerService(settings, store=store).update_summary(
        room["room_id"],
        summary_md="# Rolling Summary\n\n## Current Understanding\n\n- Designer answered the first three turns.",
    )
    state = svc.state.load(room["room_id"])
    state["summary"]["summary_version"] = 1
    svc.state.save(room["room_id"], state)

    svc.ask_room(room_id=room["room_id"], peer_ids=["poco-amo"], query="What should we ask next?", timeout_seconds=0)

    context_messages = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_REQUEST]
    metadata = context_messages[0]["payload"]["metadata"]
    assert metadata["room_summary_version"] == 1
    assert "Designer answered the first three turns." in metadata["room_summary_md"]


def test_peer_answer_prompt_uses_rendered_context_without_raw_layer_dump() -> None:
    prompt = peer_answer_prompt(
        query="From design memory, what changed to make the button responsive?",
        retrieval_bundle={
            "answer": {"text": "Designer memory says mobile CTA became full width."},
            "support": [
                {
                    "claim": "Mobile CTA became full width while desktop stayed compact.",
                    "local_ref": {"packet_id": "WP-PRIVATE", "evidence_id": "E-PRIVATE"},
                    "shared_ref": {"path": "docs/design/button.md", "symbol": "Responsive CTA"},
                    "source_peer": "designer-amo",
                }
            ],
        },
        quality={"answer_grade": True, "confidence": 0.88, "citation_count": 1, "intent_match": True, "gaps": []},
        room_context={
            "context_text": (
                "Layer 1 - Room Brief\n"
                "Topic: responsive button\n\n"
                "Layer 2 - Rolling Summary\n"
                "- Waiting for design and frontend answers.\n\n"
                "Layer 3A - Active Room Discussion\n"
                "- [context_request] initiator-amo -> designer-amo,frontend-amo: explain responsive button\n\n"
                "Layer 3B - Recent Tagged Peer Exchanges\n"
                "- [context_request] initiator-amo -> designer-amo: what did design change?"
            ),
            "layers": {"room_md": "# should not be dumped"},
        },
    )

    assert "Layer 3A - Active Room Discussion" in prompt
    assert "Layer 3B - Recent Tagged Peer Exchanges" in prompt
    assert "Mobile CTA became full width" in prompt
    assert "path=docs/design/button.md" in prompt
    assert '"layers"' not in prompt
    assert '"context_text"' not in prompt
    assert "local_ref" not in prompt
    assert "WP-PRIVATE" not in prompt
    assert "E-PRIVATE" not in prompt


def test_peer_agent_watch_returns_llm_answer_when_peer_ollama_available(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, peer_agent_allow_retrieval_only_responses=False)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    llm = FakeLlm(
        local_ready=True,
        peer_answer={"answer": "Peer found the local-first decision.", "confidence": 0.91, "answer_grade": True, "gaps": []},
    )
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


def test_peer_agent_watch_returns_retrieval_bundle_without_waiting_for_peer_llm(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    llm = FakeLlm(local_ready=False, fail_peer=True)
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=llm,
    )

    svc.watch_once()

    responses = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE]
    assert responses[0]["payload"]["metadata"]["mode"] == RESPONSE_RETRIEVAL_BUNDLE
    bundle = responses[0]["payload"]["metadata"]["retrieval_bundle"]
    assert bundle["answer"]["text"]
    assert bundle["support"]
    assert "retrieval" not in bundle
    assert llm.peer_calls == 0


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


def test_peer_agent_watch_skips_room_locked_by_another_watcher(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(fail_peer=True),
    )
    room_id = store.list_rooms()[0]["room_id"]

    with svc.state.room_lock(room_id) as acquired:
        assert acquired is True
        result = svc.watch_once()

    assert result["processed"][0]["reason"] == "room_locked"
    assert netd.sent == []
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


def test_peer_agent_response_records_stage_timing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = peer_room_with_request(tmp_path, local_node="poco-amo")
    netd = FakeNetdClient()
    svc = PeerAgentService(
        settings,
        peer_service=PeerService(settings, store=store, netd_client=netd),
        graph=FakeGraph(good_retrieval()),
        llm=FakeLlm(local_ready=False),
    )

    result = svc.watch_once()

    assert result["processed"][0]["ok"] is True
    response = [item for item in netd.sent if item["message"]["type"] == CONTEXT_RESPONSE][0]["message"]
    timing = response["payload"]["metadata"]["timing"]
    assert timing["retrieval_ms"] >= 0
    assert timing["quality_support_ms"] >= 0
    assert timing["llm_ready"] is False
    assert timing["llm_ready_ms"] >= 0
    assert timing["total_ms"] >= timing["retrieval_ms"]
    room_id = store.list_rooms()[0]["room_id"]
    state = json.loads((settings.home / ".peer" / "rooms" / room_id / "agent_state.json").read_text(encoding="utf-8"))
    assert state["response_attempts"]["req_1"]["last_timing"]["retrieval_ms"] >= 0


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


def test_peer_agent_continue_room_planner_can_ask_followup(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer", capabilities=("graph_retrieval",)))
    room = store.create_room(topic="what was local-first", participants=["poco-amo"])
    store.append_message(room["room_id"], strong_peer_response(room["room_id"], mode=RESPONSE_RETRIEVAL_BUNDLE))
    netd = FakeNetdClient()
    llm = FakeLlm(
        plan_action={
            "action": "ask_peer",
            "peer_ids": ["poco-amo"],
            "query": "Can you clarify the remaining local-first gap?",
            "reason": "Need one focused follow-up.",
            "confidence": 0.76,
        }
    )
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store, netd_client=netd), llm=llm)
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

    result = svc.continue_room(room_id=room["room_id"], timeout_seconds=60)

    assert result["ok"] is True
    assert result["action"] == "ask_peer"
    assert result["followup"]["ok"] is True
    context_messages = [item["message"] for item in netd.sent if item["message"]["type"] == CONTEXT_REQUEST]
    assert len(context_messages) == 1
    metadata = context_messages[0]["payload"]["metadata"]
    assert metadata["schema_version"] == 1
    assert metadata["agent_room_schema_version"] == 1
    assert metadata["target_peer_id"] == "poco-amo"
    assert metadata["query"] == "Can you clarify the remaining local-first gap?"
    state = json.loads((settings.home / ".peer" / "rooms" / room["room_id"] / "agent_state.json").read_text(encoding="utf-8"))
    assert state["planner_actions"][-1]["action"] == "ask_peer"


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


def test_initiator_accepts_first_peer_response_without_final_llm(tmp_path: Path) -> None:
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
    assert state["final"]["answer"] == "Peer response text with a portable commit citation."
    assert state["final"]["reason"] == "first_peer_response"
    assert llm.final_calls == 0


def test_initiator_finalizes_low_grade_retrieval_bundle_without_waiting(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    room = store.create_room(topic="what was local-first", participants=["poco-amo"])
    response = strong_peer_response(room["room_id"], mode=RESPONSE_RETRIEVAL_BUNDLE)
    response["confidence"] = 0.31
    response["metadata"]["answer_grade"] = False
    response["metadata"]["quality"] = {
        "answer_grade": False,
        "confidence": 0.31,
        "gaps": ["top hit is not clearly answer-grade"],
    }
    store.append_message(room["room_id"], response)
    llm = FakeLlm(fail_final=False)
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store), llm=llm)
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

    result = svc.watch_once()

    assert any(item.get("finalized") for item in result["processed"])
    state = json.loads((settings.home / ".peer" / "rooms" / room["room_id"] / "agent_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "finalized"
    assert state["final"]["mode"] == RESPONSE_RETRIEVAL_BUNDLE
    assert state["final"]["reason"] == "first_peer_response"
    assert state["final"]["answer"] == "Peer response text with a portable commit citation."
    assert llm.final_calls == 0


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
    assert state["final"]["mode"] == RESPONSE_RETRIEVAL_BUNDLE
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


def test_peer_agent_summarizes_after_three_completed_logical_requests(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    room = store.create_room(
        topic="responsive button discussion",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    llm = FakeLlm()
    svc = PeerAgentService(settings, peer_service=PeerService(settings, store=store), llm=llm)
    state = svc.state.load(room["room_id"])
    state["agent_managed"] = True
    state["status"] = "open"
    svc.state.save(room["room_id"], state)

    for index in range(1, 3):
        append_completed_turn(store, room["room_id"], index)

    assert svc._maybe_summarize_initiator_room(store.get_room(room["room_id"])) is None
    assert llm.summary_calls == 0

    append_completed_turn(store, room["room_id"], 3)
    result = svc._maybe_summarize_initiator_room(store.get_room(room["room_id"]))

    assert result and result["summary_updated"] is True
    assert llm.summary_calls == 1
    state = svc.state.load(room["room_id"])
    assert state["summary"]["summarized_until_request_count"] == 3
    assert svc._maybe_summarize_initiator_room(store.get_room(room["room_id"])) is None
    assert llm.summary_calls == 1


def test_peer_agent_mcp_contracts_are_registered(tmp_path: Path) -> None:
    service = MemoryMcpToolService(make_settings(tmp_path), peer_agent=FakePeerAgent())

    contracts = service.tool_contracts()
    result = service.peer_memory_ask(query="hello", timeout_seconds=0)
    followup = service.peer_room_ask(room_id="room_1", query="follow up")
    continued = service.peer_room_continue(room_id="room_1")

    assert "peer_memory_ask" in MCP_MEMORY_TOOL_CONTRACTS
    assert "peer_room_ask" in contracts["tools"]
    assert "peer_room_continue" in contracts["tools"]
    assert "peer_room_messages" in contracts["tools"]
    assert result["mode"] == "retrieval_only"
    assert followup["mode"] == "room_followup"
    assert continued["action"] == "wait"


def append_completed_turn(store: PeerStore, room_id: str, index: int) -> None:
    request_id = f"req_turn_{index}"
    store.append_message(
        room_id,
        {
            "message_id": f"msg_request_{index}",
            "type": CONTEXT_REQUEST,
            "from": "zenbook-amo",
            "from_node_id": "zenbook-amo",
            "to": ["poco-amo"],
            "to_node_ids": ["poco-amo"],
            "content": f"turn {index} question",
            "metadata": {
                "request_id": request_id,
                "logical_request_id": f"q_turn_{index}",
                "query": f"turn {index} question",
                "target_peer_id": "poco-amo",
            },
        },
    )
    store.append_message(
        room_id,
        {
            "message_id": f"msg_response_{index}",
            "type": CONTEXT_RESPONSE,
            "from": "poco-amo",
            "from_node_id": "poco-amo",
            "to": ["zenbook-amo"],
            "to_node_ids": ["zenbook-amo"],
            "content": f"turn {index} answer",
            "metadata": {"request_id": request_id},
        },
    )


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
        plan_action: dict[str, Any] | None = None,
        local_ready: bool = True,
        fail_peer: bool = False,
        fail_final: bool = False,
        fail_plan: bool = False,
    ) -> None:
        self.peer_answer = peer_answer or {"answer": "peer answer", "confidence": 0.9, "answer_grade": True, "gaps": []}
        self.final_answer = final_answer or {"answer": "final answer", "confidence": 0.9, "mode": "peer_assisted", "gaps": []}
        self.plan_action = plan_action or {"action": "finalize", "peer_ids": [], "query": "", "reason": "enough context", "confidence": 0.8}
        self.local_ready = local_ready
        self.fail_peer = fail_peer
        self.fail_final = fail_final
        self.fail_plan = fail_plan
        self.peer_calls = 0
        self.final_calls = 0
        self.plan_calls = 0
        self.summary_calls = 0

    def local_ollama_ready(self, **kwargs: Any) -> bool:
        return self.local_ready

    def provider_configured(self) -> bool:
        return False

    def generate_peer_answer(self, **kwargs: Any) -> dict[str, Any]:
        self.peer_calls += 1
        if self.fail_peer:
            raise RuntimeError("peer llm unavailable")
        return self.peer_answer

    def synthesize_final(self, **kwargs: Any) -> dict[str, Any]:
        self.final_calls += 1
        if self.fail_final:
            raise RuntimeError("final llm unavailable")
        return self.final_answer

    def plan_room_continuation(self, **kwargs: Any) -> dict[str, Any]:
        self.plan_calls += 1
        if self.fail_plan:
            raise RuntimeError("planner llm unavailable")
        return self.plan_action

    def summarize_room(self, **kwargs: Any) -> dict[str, Any]:
        self.summary_calls += 1
        return {"summary_md": "# Rolling Summary\n\n## Current Understanding\n\n- Test summary."}


class FakePeerAgent:
    def ask(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "mode": "retrieval_only", "answer": "fake", "room_id": "", "local_quality": {}, "peer_responses": [], "citations": [], "timing": {}}

    def status(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room_id": room_id}

    def ask_room(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "mode": "room_followup", "room_id": kwargs.get("room_id", ""), "peer_requests": []}

    def continue_room(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "action": "wait", "room_id": kwargs.get("room_id", "")}

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
