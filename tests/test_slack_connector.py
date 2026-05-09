from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from agent_memory_orchestrator.cli import main
from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.connectors.slack import SlackConfig, SlackConnectorService
from agent_memory_orchestrator.connectors.slack.client import SlackApiClient
from agent_memory_orchestrator.connectors.slack.config import load_slack_config, slack_secret_path
from agent_memory_orchestrator.connectors.slack.events import parse_message_envelope, should_reply_message
from agent_memory_orchestrator.connectors.slack.manifest import build_slack_manifest, slack_manifest_setup_url
from agent_memory_orchestrator.graph_triggers import detect_trigger


def test_slack_manifest_enables_socket_mode_and_message_scopes() -> None:
    manifest = build_slack_manifest(app_name="AMO Test")

    assert manifest["display_information"]["name"] == "AMO Test"
    assert manifest["settings"]["socket_mode_enabled"] is True
    scopes = manifest["oauth_config"]["scopes"]["bot"]
    assert "app_mentions:read" in scopes
    assert "chat:write" in scopes
    assert "message.channels" in manifest["settings"]["event_subscriptions"]["bot_events"]


def test_slack_setup_link_prefills_manifest() -> None:
    url = slack_manifest_setup_url(app_name="AMO Link")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "api.slack.com"
    assert query["new_app"] == ["1"]
    manifest = json.loads(query["manifest_json"][0])
    assert manifest["display_information"]["name"] == "AMO Link"
    assert manifest["settings"]["socket_mode_enabled"] is True


def test_slack_bootstrap_uses_manifest_api_without_printing_credentials(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_transport(url: str, token: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
        calls.append({"url": url, "token": token, "payload": payload, "timeout": timeout})
        return {
            "ok": True,
            "app_id": "A123",
            "credentials": {"client_secret": "secret", "signing_secret": "secret"},
            "oauth_authorize_url": "https://slack.com/oauth/v2/authorize?client_id=1",
        }

    service = SlackConnectorService(
        _settings(tmp_path),
        config=SlackConfig(enabled=True),
        client=SlackApiClient(transport=fake_transport),
    )

    result = service.bootstrap_with_config_token(config_token="xoxe.xoxp-test", app_name="AMO Bootstrap")

    assert result["ok"] is True
    assert result["app_id"] == "A123"
    assert "credentials" not in result
    assert result["credential_fields_returned"] == ["client_secret", "signing_secret"]
    assert calls[0]["url"] == "https://slack.com/api/apps.manifest.create"
    assert calls[0]["token"] == "xoxe.xoxp-test"
    payload = calls[0]["payload"]
    assert isinstance(payload, dict)
    manifest = json.loads(str(payload["manifest"]))
    assert manifest["display_information"]["name"] == "AMO Bootstrap"


def test_slack_setup_writes_config_without_leaking_tokens(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path))
    settings = Settings.load()
    svc = SlackConnectorService(settings)

    result = svc.setup(
        team_id="T123",
        bot_user_id="B123",
        capture_user_ids=["U123"],
        allowed_channels=["C123"],
        app_token="xapp-test",
        bot_token="xoxb-test",
        save_tokens=True,
        skip_token_validation=True,
    )

    assert result["ok"] is True
    config_text = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert "xapp-test" not in config_text
    assert "xoxb-test" not in config_text
    loaded = load_slack_config(settings)
    assert loaded.team_id == "T123"
    assert loaded.bot_user_id == "B123"
    assert loaded.capture_user_ids == ("U123",)
    assert json.loads(slack_secret_path(settings).read_text(encoding="utf-8")) == {
        "app_token": "xapp-test",
        "bot_token": "xoxb-test",
    }


def test_slack_message_capture_rules_for_user_and_bot_mention(tmp_path) -> None:
    settings = _settings(tmp_path)
    config = SlackConfig(
        enabled=True,
        team_id="T1",
        bot_user_id="B1",
        capture_user_ids=("U1",),
        allowed_channels=("C1",),
    )
    svc = SlackConnectorService(settings, config=config)

    captured = svc.handle_event_envelope(_message_envelope(user="U1", text="I am starting the task"))
    mentioned = svc.handle_event_envelope(_message_envelope(user="U2", text="<@B1> answer this"))
    skipped = svc.handle_event_envelope(_message_envelope(user="U2", text="irrelevant chatter"))

    assert captured["captured"] is True
    assert captured["reply_required"] is False
    assert captured["session_id"] == "slack:T1:C1:111.222"
    assert mentioned["captured"] is True
    assert mentioned["reply_required"] is True
    assert skipped["captured"] is False
    assert skipped["reason"] == "not_relevant"


def test_slack_message_evidence_is_local_raw_jsonl(tmp_path) -> None:
    settings = _settings(tmp_path)
    config = SlackConfig(enabled=True, team_id="T1", bot_user_id="B1", capture_user_ids=("U1",))
    svc = SlackConnectorService(settings, config=config)

    result = svc.handle_event_envelope(_message_envelope(user="U1", text="Keep this Slack decision"))

    assert result["captured"] is True
    evidence_path = settings.evidence_dir / f"{result['evidence']['created_at'][:10]}.jsonl"
    rows = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["source_app"] == "slack"
    assert rows[-1]["event_name"] == "slack_message"
    assert rows[-1]["payload"]["metadata"]["capture_reason"] == "captured_user_message"
    assert rows[-1]["payload"]["message"] == "Keep this Slack decision"


def test_slack_finalize_event_triggers_graph_drain_window(tmp_path) -> None:
    settings = _settings(tmp_path)
    svc = SlackConnectorService(settings, config=SlackConfig(enabled=True))

    result = svc.finalize_session(session_id="slack:T1:C1:111.222", reason="idle_timeout", message_count=2)

    assert result["ok"] is True
    record = {
        "event_name": "connector_session_finalize",
        "payload": {
            "event_type": "connector_session_finalize",
            "message": "Finalize Slack connector session: idle_timeout",
        },
    }
    decision = detect_trigger(record)
    assert decision.should_process is True
    assert decision.trigger_type == "connector_finalize"


def test_slack_cli_manifest_and_setup(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path))

    assert main(["slack", "manifest", "--app-name", "AMO CLI"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["display_information"]["name"] == "AMO CLI"

    code = main(
        [
            "slack",
            "setup",
            "--team-id",
            "T1",
            "--bot-user-id",
            "B1",
            "--capture-user-id",
            "U1",
            "--app-token",
            "xapp-local",
            "--bot-token",
            "xoxb-local",
            "--skip-token-validation",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["ok"] is True
    assert out["config"]["team_id"] == "T1"
    assert out["token_hint"]


def test_slack_cli_setup_link_does_not_require_amo_home(capsys) -> None:
    assert main(["slack", "setup-link", "--app-name", "AMO Link CLI"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["ok"] is True
    assert "manifest_json=" in out["url"]


def test_slack_reply_rule_is_mention_only() -> None:
    config = SlackConfig(enabled=True, bot_user_id="B1", reply_only_when_mentioned=True)
    direct_message = parse_message_envelope(_message_envelope(channel="D1", user="U1", text="hello"))
    mentioned = parse_message_envelope(_message_envelope(channel="C1", user="U1", text="<@B1> hello"))

    assert direct_message is not None
    assert mentioned is not None
    assert should_reply_message(direct_message, config) is False
    assert should_reply_message(mentioned, config) is True


def _settings(tmp_path) -> Settings:
    home = tmp_path / "amo"
    return Settings(
        home=home,
        db_path=home / ".data" / "agent_memory.db",
        export_dir=home / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=256,
        embedding_model="BAAI/bge-m3",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="auto",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=home / ".graph" / "amo.kuzu",
        evidence_dir=home / ".evidence",
    )


def _message_envelope(
    *,
    channel: str = "C1",
    user: str = "U1",
    text: str = "hello",
    team: str = "T1",
) -> dict[str, object]:
    return {
        "type": "events_api",
        "envelope_id": "env1",
        "payload": {
            "team_id": team,
            "event": {
                "type": "message",
                "channel": channel,
                "user": user,
                "text": text,
                "ts": "111.222",
            },
        },
    }
