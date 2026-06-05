from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.auth import wrap_payload
from agent_memory_orchestrator.peer.cards import build_peer_card
from agent_memory_orchestrator.peer.invites import build_peer_invite, invite_token_hash, invite_token_proof, peer_card_sha256
from agent_memory_orchestrator.peer.models import PeerConfig, PeerNode
from agent_memory_orchestrator.peer.service import PeerService
from agent_memory_orchestrator.peer.store import PeerStore


def test_peer_room_invite_creates_three_layer_context_files(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "initiator")
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo", display_name="Zenbook")
    store.add_peer(PeerNode(node_id="poco-amo", base_url="http://100.76.18.75:8787", capabilities=("graph_retrieval",)))

    result = PeerService(settings, store=store).open_room(
        topic="why did graph_service.py change?",
        peer_ids=["poco-amo"],
        send_invites=False,
    )

    assert result["ok"] is True
    room = result["room"]
    room_dir = settings.home / ".peer" / "rooms" / room["room_id"]
    assert (room_dir / "room.md").exists()
    assert (room_dir / "rolling_summary.md").exists()
    assert (room_dir / "transcript.jsonl").exists()
    room_md = (room_dir / "room.md").read_text(encoding="utf-8")
    assert "why did graph_service.py change?" in room_md
    assert "Layer 1: this room.md brief" in room_md
    assert "Layer 2: initiator-owned rolling_summary.md" in room_md
    assert "Layer 3A: active initiator-led request/response discussion" in room_md
    assert "Layer 3B: tagged initiator-peer exchanges" in room_md
    assert "Peers auto-respond only when tagged" in room_md


def test_peer_config_accepts_libp2p_peer_identity_without_legacy_base_url(tmp_path: Path) -> None:
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")

    config = store.add_peer(
        PeerNode(
            node_id="poco-amo",
            peer_id="12D3KooWPeer",
            multiaddrs=("/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer",),
            relay_addrs=("/ip4/relay/tcp/4001/p2p/relay/p2p-circuit/p2p/12D3KooWPeer",),
            rendezvous_addr="/ip4/127.0.0.1/tcp/9000/p2p/12D3KooWRendezvous",
            rendezvous_namespace="amo-team",
        )
    )

    peer = config.peer_by_id("poco-amo")
    assert peer is not None
    assert peer.base_url == ""
    assert peer.peer_id == "12D3KooWPeer"
    assert peer.multiaddrs == ("/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer",)
    assert peer.rendezvous_namespace == "amo-team"


def test_peer_join_request_auto_approves_with_valid_invite_token(tmp_path: Path) -> None:
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")
    invite = _save_test_invite(store, auto_approve=True, token="join-secret")
    peer_card = _peer_card("mac-amo", "12D3KooWMac")
    netd = FakeNetdClient()

    result = PeerService(store.settings, store=store, netd_client=netd).receive_netd_envelope(
        _join_request_envelope(invite=invite, peer_card=peer_card, token="join-secret")
    )

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["mode"] == "auto_approved"
    peer = store.load_config().peer_by_id("mac-amo")
    assert peer is not None
    assert peer.peer_id == "12D3KooWMac"
    assert store.get_peer_invite_record(invite["invite_id"])["status"] == "accepted"
    assert netd.sent[-1]["message"]["type"] == "peer_join_accepted"


def test_peer_join_request_can_wait_for_manual_approval(tmp_path: Path) -> None:
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")
    invite = _save_test_invite(store, auto_approve=False, token="join-secret")
    peer_card = _peer_card("mac-amo", "12D3KooWMac")

    result = PeerService(store.settings, store=store).receive_netd_envelope(
        _join_request_envelope(invite=invite, peer_card=peer_card, token="join-secret")
    )

    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["mode"] == "pending_approval"
    assert store.load_config().peer_by_id("mac-amo") is None
    pending = store.list_join_requests(status="pending")
    assert len(pending) == 1
    assert pending[0]["peer_card"]["node_id"] == "mac-amo"


def test_peer_join_request_rejects_bad_token(tmp_path: Path) -> None:
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")
    invite = _save_test_invite(store, auto_approve=True, token="join-secret")
    peer_card = _peer_card("mac-amo", "12D3KooWMac")

    result = PeerService(store.settings, store=store).receive_netd_envelope(
        _join_request_envelope(invite=invite, peer_card=peer_card, token="wrong-secret")
    )

    assert result["ok"] is False
    assert "token proof" in result["error"]
    assert store.load_config().peer_by_id("mac-amo") is None


def test_trusted_peer_accepts_invite_and_records_messages(tmp_path: Path) -> None:
    initiator_store = PeerStore(make_settings(tmp_path / "initiator"))
    initiator_store.init_config(node_id="zenbook-amo")
    initiator_store.add_peer(PeerNode(node_id="poco-amo", base_url="http://100.76.18.75:8787"))
    room = PeerService(initiator_store.settings, store=initiator_store).open_room(
        topic="find relevant AMO memory",
        peer_ids=["poco-amo"],
        send_invites=False,
    )["room"]
    invite = initiator_store.invite_payload(room["room_id"])

    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", base_url="http://100.82.177.7:8787", trust="trusted"))
    peer_service = PeerService(peer_store.settings, store=peer_store)

    accepted = peer_service.receive_invite(invite)
    assert accepted["ok"] is True
    assert accepted["accepted"] is True

    message = peer_service.receive_message(
        {
            "room_id": room["room_id"],
            "type": "context_response",
            "from": "zenbook-amo",
            "content": "Need packet citations only.",
            "citations": ["WP0030"],
            "confidence": 0.8,
        }
    )

    assert message["ok"] is True
    detail = peer_service.room_detail(room["room_id"])["room"]
    assert [item["type"] for item in detail["messages"]] == ["room_invite_received", "context_response"]
    assert detail["messages"][1]["citations"] == ["WP0030"]


def test_open_room_sends_libp2p_invite_through_netd(tmp_path: Path) -> None:
    netd = FakeNetdClient()
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")
    store.add_peer(
        PeerNode(
            node_id="poco-amo",
            peer_id="12D3KooWPeer",
            multiaddrs=("/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer",),
        )
    )

    result = PeerService(store.settings, store=store, netd_client=netd).open_room(
        topic="why did graph_service.py change?",
        peer_ids=["poco-amo"],
        send_invites=True,
    )

    assert result["ok"] is True
    assert result["deliveries"][0]["transport"] == "libp2p"
    assert netd.connected == ["/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer"]
    assert netd.sent[0]["to_peer_id"] == "12D3KooWPeer"
    assert netd.sent[0]["message"]["type"] == "room_invite"
    assert netd.sent[0]["message"]["payload"]["room_md_sha256"] == result["room"]["room_md_sha256"]


def test_process_netd_inbox_accepts_invite_and_deduplicates(tmp_path: Path) -> None:
    initiator_store = PeerStore(make_settings(tmp_path / "initiator"))
    initiator_store.init_config(node_id="zenbook-amo")
    room = PeerService(initiator_store.settings, store=initiator_store).open_room(
        topic="check local memory",
        peer_ids=["poco-amo"],
        send_invites=False,
    )["room"]
    invite = initiator_store.invite_payload(room["room_id"])

    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(
        PeerNode(
            node_id="zenbook-amo",
            peer_id="12D3KooWInitiator",
            trust="trusted",
            shared_secret_env="AMO_PEER_SECRET_NOT_SET",
        )
    )
    netd = FakeNetdClient(
        messages=[
            {
                "amo_peer_envelope_version": 1,
                "from_node_id": "zenbook-amo",
                "created_at": "2026-05-18T00:00:00Z",
                "payload_sha256": "abc",
                "signature": "hmac-sha256:test",
                "message": {
                    "type": "room_invite",
                    "room_id": room["room_id"],
                    "from_node_id": "zenbook-amo",
                    "to_node_id": "poco-amo",
                    "payload": invite,
                },
            }
        ]
    )

    svc = PeerService(peer_store.settings, store=peer_store, netd_client=netd)
    first = svc.process_netd_inbox()
    second = svc.process_netd_inbox()

    assert first["results"][0]["accepted"] is True
    assert second["results"][0]["skipped"] is True
    accepted = peer_store.get_room(room["room_id"])
    assert accepted["topic"] == "check local memory"


def test_send_message_to_peer_uses_libp2p_netd(tmp_path: Path) -> None:
    netd = FakeNetdClient()
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")
    store.add_peer(PeerNode(node_id="poco-amo", peer_id="12D3KooWPeer"))
    room = PeerService(store.settings, store=store).open_room(
        topic="collect answer",
        peer_ids=["poco-amo"],
        send_invites=False,
    )["room"]

    result = PeerService(store.settings, store=store, netd_client=netd).send_message_to_peer(
        peer_id="poco-amo",
        room_id=room["room_id"],
        content="What do you remember?",
        citations=["WP0030"],
        confidence=0.7,
    )

    assert result["ok"] is True
    assert netd.sent[0]["to_peer_id"] == "12D3KooWPeer"
    assert netd.sent[0]["message"]["type"] == "context_request"
    assert netd.sent[0]["message"]["payload"]["content"] == "What do you remember?"
    assert store.get_room(room["room_id"])["messages"][-1]["content"] == "What do you remember?"


def test_peer_service_uses_managed_netd_api_url_from_state(tmp_path: Path) -> None:
    store = PeerStore(make_settings(tmp_path / "initiator"))
    store.init_config(node_id="zenbook-amo")
    netd_dir = store.settings.home / ".peer" / "netd"
    netd_dir.mkdir(parents=True)
    (netd_dir / "netd.json").write_text(
        '{"pid": 999999, "api_url": "http://127.0.0.1:8799"}',
        encoding="utf-8",
    )

    client = PeerService(store.settings, store=store)._netd()

    assert client.base_url == "http://127.0.0.1:8799"


def test_signed_invite_is_accepted_when_peer_secret_matches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMO_PEER_ZENBOOK_SECRET", "test-secret")
    initiator_store = PeerStore(make_settings(tmp_path / "initiator"))
    initiator_store.init_config(node_id="zenbook-amo")
    room = PeerService(initiator_store.settings, store=initiator_store).open_room(
        topic="signed room",
        peer_ids=["poco-amo"],
        send_invites=False,
    )["room"]
    invite = initiator_store.invite_payload(room["room_id"])
    envelope = wrap_payload(payload=invite, from_node_id="zenbook-amo", secret="test-secret")

    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(
        PeerNode(
            node_id="zenbook-amo",
            base_url="http://100.82.177.7:8787",
            trust="trusted",
            shared_secret_env="AMO_PEER_ZENBOOK_SECRET",
        )
    )

    accepted = PeerService(peer_store.settings, store=peer_store).receive_invite(envelope)

    assert accepted["ok"] is True
    assert accepted["auth"]["authenticated"] is True
    assert accepted["auth"]["auth"] == "hmac-sha256"


def test_unsigned_invite_is_denied_when_peer_secret_is_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMO_PEER_ZENBOOK_SECRET", "test-secret")
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(
        PeerNode(
            node_id="zenbook-amo",
            base_url="http://100.82.177.7:8787",
            trust="trusted",
            shared_secret_env="AMO_PEER_ZENBOOK_SECRET",
        )
    )

    result = PeerService(peer_store.settings, store=peer_store).receive_invite(
        {
            "room_id": "room_test",
            "topic": "unsigned invite",
            "initiator_node_id": "zenbook-amo",
            "participants": ["zenbook-amo", "poco-amo"],
            "room_md": "# room",
        }
    )

    assert result["ok"] is False
    assert result["accepted"] is False
    assert "signed envelope required" in result["error"]


def test_untrusted_initiator_invite_is_denied(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    service = PeerService(peer_store.settings, store=peer_store)

    result = service.receive_invite(
        {
            "room_id": "room_test",
            "topic": "private memory request",
            "initiator_node_id": "unknown-amo",
            "participants": ["unknown-amo", "poco-amo"],
            "room_md": "# room",
        }
    )

    assert result["ok"] is False
    assert result["accepted"] is False
    assert "not trusted" in result["error"]


def test_untrusted_message_sender_is_denied(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", base_url="http://100.82.177.7:8787", trust="trusted"))
    service = PeerService(peer_store.settings, store=peer_store)
    accepted = service.receive_invite(
        {
            "room_id": "room_test",
            "topic": "trusted room",
            "initiator_node_id": "zenbook-amo",
            "participants": ["zenbook-amo", "poco-amo"],
            "room_md": "# room",
        }
    )
    assert accepted["ok"] is True

    result = service.receive_message(
        {
            "room_id": "room_test",
            "type": "context_response",
            "from": "unknown-amo",
            "content": "spoofed response",
        }
    )

    assert result["ok"] is False
    assert "sender is not trusted" in result["error"]


def test_non_participant_message_sender_is_denied(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", base_url="http://100.82.177.7:8787", trust="trusted"))
    peer_store.add_peer(PeerNode(node_id="ui-amo", base_url="http://100.82.177.8:8787", trust="trusted"))
    service = PeerService(peer_store.settings, store=peer_store)
    accepted = service.receive_invite(
        {
            "room_id": "room_test",
            "topic": "trusted room",
            "initiator_node_id": "zenbook-amo",
            "participants": ["zenbook-amo", "poco-amo"],
            "room_md": "# room",
        }
    )
    assert accepted["ok"] is True

    result = service.receive_message(
        {
            "room_id": "room_test",
            "type": "context_response",
            "from": "ui-amo",
            "content": "not in this room",
        }
    )

    assert result["ok"] is False
    assert "not a room participant" in result["error"]


def test_unsigned_netd_message_is_denied_when_peer_requires_secret(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(
        PeerNode(
            node_id="zenbook-amo",
            peer_id="12D3KooWPeer",
            trust="trusted",
            shared_secret_env="AMO_PEER_ZENBOOK_SECRET",
        )
    )
    room = peer_store.create_room(
        topic="trusted room",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    service = PeerService(peer_store.settings, store=peer_store)

    result = service.receive_message(
        {
            "room_id": room["room_id"],
            "type": "context_response",
            "from": "zenbook-amo",
            "content": "unsigned response",
        },
        transport_auth={"authenticated": False, "auth": "netd:none", "from_node_id": "zenbook-amo"},
    )

    assert result["ok"] is False
    assert "signed envelope required" in result["error"]


def test_netd_peer_agent_message_rejects_remote_peer_id_mismatch(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", peer_id="12D3KooWGood", trust="trusted"))
    room = peer_store.create_room(
        topic="trusted room",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    service = PeerService(peer_store.settings, store=peer_store)

    result = service.receive_netd_envelope(
        _context_request_envelope(
            room_id=room["room_id"],
            remote_peer_id="12D3KooWBad",
        )
    )

    assert result["ok"] is False
    assert "remote peer id mismatch" in result["error"]


def test_netd_peer_agent_message_requires_remote_peer_id_or_signature(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", peer_id="12D3KooWGood", trust="trusted"))
    room = peer_store.create_room(
        topic="trusted room",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    service = PeerService(peer_store.settings, store=peer_store)

    result = service.receive_netd_envelope(_context_request_envelope(room_id=room["room_id"]))

    assert result["ok"] is False
    assert "remote peer id or signed envelope required" in result["error"]


def test_process_netd_inbox_does_not_hide_failed_peer_agent_envelope(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", peer_id="12D3KooWGood", trust="trusted"))
    room = peer_store.create_room(
        topic="trusted room",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    netd = FakeNetdClient(messages=[_context_request_envelope(room_id=room["room_id"])])
    service = PeerService(peer_store.settings, store=peer_store, netd_client=netd)

    first = service.process_netd_inbox()
    second = service.process_netd_inbox()

    assert first["results"][0]["ok"] is False
    assert first["results"][0]["processed"] is False
    assert "remote peer id or signed envelope required" in first["results"][0]["error"]
    assert second["results"][0]["ok"] is False
    assert second["results"][0]["processed"] is False
    assert peer_store.load_processed_netd_ids() == set()


def test_netd_peer_agent_message_without_peer_id_requires_signature(tmp_path: Path) -> None:
    peer_store = PeerStore(make_settings(tmp_path / "peer"))
    peer_store.init_config(node_id="poco-amo")
    peer_store.add_peer(PeerNode(node_id="zenbook-amo", base_url="http://127.0.0.1:8787", trust="trusted"))
    room = peer_store.create_room(
        topic="trusted room",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    service = PeerService(peer_store.settings, store=peer_store)

    result = service.receive_netd_envelope(_context_request_envelope(room_id=room["room_id"]))

    assert result["ok"] is False
    assert "signed envelope required for peer-agent message without peer_id" in result["error"]


def test_context_pack_uses_pairwise_recent_messages_for_peer(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "initiator")
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    room = PeerService(settings, store=store).open_room(
        topic="route low-confidence memory question",
        peer_ids=["poco-amo", "ui-amo"],
        send_invites=False,
    )["room"]
    svc = PeerService(settings, store=store)
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="zenbook-amo",
        to_node_ids=["poco-amo", "ui-amo"],
        message_type="peer_message",
        content="Shared room note: compare graph retrieval and UI memory.",
        metadata={"audience": "group"},
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="zenbook-amo",
        to_node_ids=["poco-amo"],
        message_type="context_request",
        content="Can you check graph retrieval memory?",
        metadata={
            "logical_request_id": "q_graph_retrieval",
            "request_id": "req_poco_graph",
            "query": "Can you check graph retrieval memory?",
            "target_peer_id": "poco-amo",
        },
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="ui-amo",
        to_node_ids=["zenbook-amo"],
        message_type="peer_message",
        content="Unrelated UI reply should not enter poco context.",
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="poco-amo",
        to_node_ids=["zenbook-amo"],
        message_type="context_response",
        content="I found WP0030.",
        citations=["WP0030"],
        confidence=0.86,
        metadata={"request_id": "req_poco_graph"},
    )

    pack = svc.context_pack(room["room_id"], viewer_node_id="poco-amo")["context"]

    assert pack["role"] == "peer"
    assert "route low-confidence memory question" in pack["layers"]["room_md"]
    roster_ids = {item["node_id"] for item in pack["layers"]["room_roster"]}
    assert roster_ids == {"poco-amo", "ui-amo", "zenbook-amo"}
    assert "Shared room note" not in pack["context_text"]
    assert any("Shared room note" in item["content"] for item in pack["layers"]["group_recent_messages"])
    assert "Layer 3A - Active Room Discussion" in pack["context_text"]
    assert "Can you check graph retrieval memory?" in pack["context_text"]
    assert "I found WP0030." in pack["context_text"]
    assert "Unrelated UI reply" not in pack["context_text"]
    assert [item["content"] for item in pack["layers"]["active_recent_messages"]] == [
        "Can you check graph retrieval memory?",
        "I found WP0030.",
    ]
    assert pack["policy_projection"]["share_boundary"]


def test_context_pack_uses_deduped_orchestration_view_for_initiator(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "initiator")
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    room = PeerService(settings, store=store).open_room(
        topic="collect peer answers",
        peer_ids=["poco-amo", "ui-amo"],
        send_invites=False,
    )["room"]
    svc = PeerService(settings, store=store)
    for peer_id in ("poco-amo", "ui-amo"):
        svc.append_message(
            room_id=room["room_id"],
            from_node_id="zenbook-amo",
            to_node_ids=[peer_id],
            message_type="context_request",
            content="Can you check relay setup memory?",
            metadata={
                "logical_request_id": "q_relay_setup",
                "request_id": f"req_{peer_id}",
                "query": "Can you check relay setup memory?",
                "target_peer_id": peer_id,
            },
        )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="poco-amo",
        to_node_ids=["zenbook-amo"],
        message_type="context_response",
        content="Poco found relay setup commits.",
        confidence=0.82,
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="ui-amo",
        to_node_ids=["zenbook-amo"],
        message_type="context_response",
        content="UI found no related memory.",
        confidence=0.31,
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="zenbook-amo",
        message_type="final_synthesis",
        content="Local final answer should not replace peer orchestration context.",
        metadata={"local_only": True, "audience": "local"},
    )

    pack = svc.context_pack(room["room_id"], viewer_node_id="zenbook-amo")["context"]
    recent = pack["layers"]["recent_messages"]
    recent_contents = [item["content"] for item in recent]
    request_groups = [item for item in recent if item["type"] == "context_request_group"]

    assert pack["role"] == "initiator"
    assert len(request_groups) == 1
    assert request_groups[0]["to_node_ids"] == ["poco-amo", "ui-amo"]
    assert request_groups[0]["metadata"]["request_count"] == 2
    assert recent_contents.count("Can you check relay setup memory?") == 1
    assert "Poco found relay setup commits." in recent_contents
    assert "UI found no related memory." in recent_contents
    assert "Local final answer should not replace peer orchestration context." not in recent_contents
    assert pack["layers"]["pairwise_recent_messages"] == recent


def test_context_pack_excludes_answered_context_requests_from_open_questions(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "initiator")
    store = PeerStore(settings)
    store.init_config(node_id="zenbook-amo")
    room = PeerService(settings, store=store).open_room(
        topic="collect peer answers",
        peer_ids=["poco-amo"],
        send_invites=False,
    )["room"]
    svc = PeerService(settings, store=store)
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="zenbook-amo",
        to_node_ids=["poco-amo"],
        message_type="context_request",
        content="Answered question?",
        metadata={"request_id": "req_answered", "query": "Answered question?"},
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="poco-amo",
        to_node_ids=["zenbook-amo"],
        message_type="context_response",
        content="Answered.",
        metadata={"request_id": "req_answered"},
    )
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="zenbook-amo",
        to_node_ids=["poco-amo"],
        message_type="context_request",
        content="Still pending?",
        metadata={"request_id": "req_pending", "query": "Still pending?"},
    )

    initiator_pack = svc.context_pack(room["room_id"], viewer_node_id="zenbook-amo")["context"]
    peer_pack = svc.context_pack(room["room_id"], viewer_node_id="poco-amo")["context"]

    assert [item["request_id"] for item in initiator_pack["layers"]["open_questions"]] == ["req_pending"]
    assert [item["request_id"] for item in peer_pack["layers"]["open_questions"]] == ["req_pending"]


def test_context_pack_uses_initiator_shared_summary_for_peer(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "peer")
    store = PeerStore(settings)
    store.init_config(node_id="poco-amo")
    room = store.create_room(
        topic="responsive button discussion",
        participants=["zenbook-amo", "poco-amo"],
        initiator_node_id="zenbook-amo",
    )
    svc = PeerService(settings, store=store)
    svc.append_message(
        room_id=room["room_id"],
        from_node_id="zenbook-amo",
        to_node_ids=["poco-amo"],
        message_type="context_request",
        content="What should we ask next?",
        metadata={
            "request_id": "req_after_summary",
            "query": "What should we ask next?",
            "room_summary_version": 1,
            "room_summary_md": "# Rolling Summary\n\n- Initiator already combined the first three peer answers.",
        },
    )

    pack = svc.context_pack(room["room_id"], viewer_node_id="poco-amo")["context"]

    assert "Initiator already combined the first three peer answers." in pack["layers"]["rolling_summary_md"]
    assert "Initiator already combined the first three peer answers." in pack["context_text"]


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="disabled",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=tmp_path / "graph" / "amo.kuzu",
        evidence_dir=tmp_path / "evidence",
    )


class FakeNetdClient:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self.connected: list[str] = []
        self.sent: list[dict] = []
        self._messages = messages or []

    def connect(self, addr: str) -> dict:
        self.connected.append(addr)
        return {"ok": True}

    def send_raw(self, to_peer_id: str, message: dict) -> dict:
        self.sent.append({"to_peer_id": to_peer_id, "message": message})
        return {"ok": True, "envelope": {"message": message}}

    def messages(self) -> list[dict]:
        return list(self._messages)

    def rendezvous_discover(self, addr: str, namespace: str, limit: int = 20, connect: bool = True) -> list[dict]:
        return [{"peer_id": "12D3KooWPeer", "addrs": [addr], "namespace": namespace, "connect": connect}]

    def health(self) -> dict:
        return {
            "peer_id": "12D3KooWFake",
            "listen_addrs": ["/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWFake"],
            "relay_addrs": [],
        }


def _save_test_invite(store: PeerStore, *, auto_approve: bool, token: str) -> dict:
    card = build_peer_card(
        config=PeerConfig(node_id="zenbook-amo"),
        netd_health={
            "peer_id": "12D3KooWHost",
            "listen_addrs": ["/ip4/127.0.0.1/tcp/9000/p2p/12D3KooWHost"],
        },
    )
    invite = build_peer_invite(card=card, auto_approve=auto_approve, token=token)
    store.save_peer_invite_record(
        {
            "invite_id": invite["invite_id"],
            "created_at": invite["created_at"],
            "expires_at": invite["expires_at"],
            "created_by_node_id": invite["created_by_node_id"],
            "recommended_trust": invite["recommended_trust"],
            "shared_secret_env": invite["shared_secret_env"],
            "auto_approve": invite["auto_approve"],
            "max_uses": invite["max_uses"],
            "used_count": 0,
            "status": "pending",
            "token_hash": invite_token_hash(token),
            "card_sha256": invite["card_sha256"],
        }
    )
    return invite


def _peer_card(node_id: str, peer_id: str) -> dict:
    return build_peer_card(
        config=PeerConfig(node_id=node_id, capabilities=("graph_retrieval",)),
        netd_health={
            "peer_id": peer_id,
            "listen_addrs": [f"/ip4/127.0.0.1/tcp/9002/p2p/{peer_id}"],
        },
    )


def _join_request_envelope(*, invite: dict, peer_card: dict, token: str) -> dict:
    return {
        "from_node_id": peer_card["node_id"],
        "payload_sha256": "test",
        "message": {
            "type": "peer_join_request",
            "from_node_id": peer_card["node_id"],
            "payload": {
                "type": "peer_join_request",
                "invite_id": invite["invite_id"],
                "token_proof": invite_token_proof(token),
                "peer_card": peer_card,
                "peer_card_sha256": peer_card_sha256(peer_card),
                "requested_trust": "trusted",
            },
        },
    }


def _context_request_envelope(*, room_id: str, remote_peer_id: str = "") -> dict:
    envelope = {
        "amo_peer_envelope_version": 1,
        "from_node_id": "zenbook-amo",
        "payload_sha256": "test",
        "message": {
            "type": "context_request",
            "room_id": room_id,
            "from_node_id": "zenbook-amo",
            "to_node_id": "poco-amo",
            "payload": {
                "room_id": room_id,
                "type": "context_request",
                "from_node_id": "zenbook-amo",
                "to_node_ids": ["poco-amo"],
                "content": "what do you remember?",
                "metadata": {"schema_version": 1, "request_id": "req_1"},
            },
        },
    }
    if remote_peer_id:
        envelope["remote_peer_id"] = remote_peer_id
    return envelope
