from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from ....config import Settings
from ....evidence.raw_store import RawEvidenceStore
from .client import SlackApiClient, SlackApiError
from .config import SlackConfig, load_slack_config, token_presence, validate_token_prefixes, write_slack_config
from .events import (
    SlackMessage,
    finalize_connector_event,
    message_to_connector_event,
    parse_message_envelope,
    should_capture_message,
    should_reply_message,
)
from .manifest import build_slack_manifest, slack_manifest_json, slack_manifest_setup_url


class SlackConnectorError(RuntimeError):
    pass


SLACK_REPLY_CHAR_LIMIT = 2800


class SlackConnectorService:
    def __init__(
        self,
        settings: Settings,
        *,
        config: SlackConfig | None = None,
        evidence_store: RawEvidenceStore | None = None,
        client: SlackApiClient | None = None,
    ) -> None:
        self.settings = settings
        self.config = config or load_slack_config(settings)
        self.evidence = evidence_store or RawEvidenceStore(settings.evidence_dir)
        self.client = client or SlackApiClient(app_token=self.config.app_token, bot_token=self.config.bot_token)

    def manifest(self, *, app_name: str = "Agent Memory Orchestrator") -> dict[str, Any]:
        return build_slack_manifest(app_name=app_name)

    def write_manifest(self, path: Path, *, app_name: str = "Agent Memory Orchestrator") -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(slack_manifest_json(app_name=app_name), encoding="utf-8")
        return {"ok": True, "path": str(path.resolve())}

    def setup_link(self, *, app_name: str = "Agent Memory Orchestrator") -> dict[str, Any]:
        return {
            "ok": True,
            "url": slack_manifest_setup_url(app_name=app_name),
            "next_steps": [
                "Open the URL.",
                "Select the Slack workspace.",
                "Review and create the app.",
                "Install the app to the workspace.",
                "Copy the app-level xapp token and bot xoxb token into `amo-cli slack setup`.",
            ],
        }

    def bootstrap_with_config_token(
        self,
        *,
        config_token: str,
        app_name: str = "Agent Memory Orchestrator",
        team_id: str = "",
    ) -> dict[str, Any]:
        try:
            result = self.client.create_app_from_manifest(
                config_token=config_token,
                manifest=build_slack_manifest(app_name=app_name),
                team_id=team_id,
            )
        except SlackApiError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "app_id": result.get("app_id"),
            "oauth_authorize_url": result.get("oauth_authorize_url"),
            "credential_fields_returned": sorted((result.get("credentials") or {}).keys())
            if isinstance(result.get("credentials"), dict)
            else [],
            "next_steps": [
                "Open oauth_authorize_url and approve installation.",
                "Copy the Bot User OAuth Token from OAuth & Permissions if Slack does not return it through OAuth.",
                "Create or copy the app-level xapp token under Basic Information > App-Level Tokens.",
                "Run `amo-cli slack setup --save-tokens ...` with the xapp and xoxb tokens.",
            ],
        }

    def setup(
        self,
        *,
        team_id: str = "",
        bot_user_id: str = "",
        capture_user_ids: list[str] | None = None,
        allowed_channels: list[str] | None = None,
        session_idle_minutes: int = 30,
        app_token: str = "",
        bot_token: str = "",
        save_tokens: bool = False,
        skip_token_validation: bool = False,
    ) -> dict[str, Any]:
        app_token = app_token or self.config.app_token
        bot_token = bot_token or self.config.bot_token
        validation = self.validate_tokens(app_token=app_token, bot_token=bot_token, skip_network=skip_token_validation)
        if not validation["ok"]:
            return {"ok": False, "validation": validation}

        configured = SlackConfig(
            enabled=True,
            mode="socket_mode",
            team_id=team_id or self.config.team_id,
            bot_user_id=bot_user_id or self.config.bot_user_id,
            capture_user_ids=tuple(capture_user_ids if capture_user_ids is not None else self.config.capture_user_ids),
            allowed_channels=tuple(allowed_channels if allowed_channels is not None else self.config.allowed_channels),
            reply_only_when_mentioned=True,
            session_idle_minutes=max(1, int(session_idle_minutes or self.config.session_idle_minutes or 30)),
            app_token=app_token,
            bot_token=bot_token,
        )
        write_result = write_slack_config(
            self.settings,
            configured,
            app_token=app_token,
            bot_token=bot_token,
            save_tokens=save_tokens,
        )
        self.config = configured
        self.client = SlackApiClient(app_token=app_token, bot_token=bot_token)
        return {
            "ok": True,
            "config": configured.public_dict(),
            "validation": validation,
            **write_result,
            "token_hint": None
            if save_tokens
            else "Tokens were not saved. Export AMO_SLACK_APP_TOKEN and AMO_SLACK_BOT_TOKEN before running.",
        }

    def status(self) -> dict[str, Any]:
        prefix_check = validate_token_prefixes(self.config.app_token, self.config.bot_token)
        return {
            "ok": True,
            "config": self.config.public_dict(),
            "tokens": token_presence(self.config),
            "prefix_check": prefix_check,
        }

    def validate_tokens(self, *, app_token: str, bot_token: str, skip_network: bool = False) -> dict[str, Any]:
        prefix_check = validate_token_prefixes(app_token, bot_token)
        if not prefix_check["ok"]:
            return {"ok": False, "prefix": prefix_check, "network": None}
        if skip_network:
            return {"ok": True, "prefix": prefix_check, "network": "skipped"}
        client = SlackApiClient(app_token=app_token, bot_token=bot_token, transport=self.client.transport)
        try:
            auth = client.auth_test() if bot_token else {}
        except SlackApiError as exc:
            return {"ok": False, "prefix": prefix_check, "network": str(exc)}
        return {"ok": True, "prefix": prefix_check, "network": {"auth_test": _safe_auth_test(auth)}}

    def handle_event_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": True, "captured": False, "reason": "slack_connector_disabled"}
        message = parse_message_envelope(envelope)
        if message is None:
            return {"ok": True, "captured": False, "reason": "unsupported_slack_event"}
        should_capture, reason = should_capture_message(message, self.config)
        if not should_capture:
            return {
                "ok": True,
                "captured": False,
                "reason": reason,
                "session_id": message.session_id,
                "external_id": message.external_id,
            }
        reply_required = should_reply_message(message, self.config)
        connector_event = message_to_connector_event(message, capture_reason=reason, reply_required=reply_required)
        evidence = self.evidence.append(
            connector_event.as_evidence_payload(),
            session_id=connector_event.session_id,
            source_app=connector_event.source_app,
            event_name=connector_event.event_type,
        )
        return {
            "ok": True,
            "captured": True,
            "reason": reason,
            "reply_required": reply_required,
            "session_id": connector_event.session_id,
            "external_id": connector_event.external_id,
            "evidence": evidence.as_dict(),
        }

    def finalize_session(self, *, session_id: str, reason: str = "idle_timeout", message_count: int = 0) -> dict[str, Any]:
        connector_event = finalize_connector_event(session_id=session_id, reason=reason, message_count=message_count)
        evidence = self.evidence.append(
            connector_event.as_evidence_payload(),
            session_id=connector_event.session_id,
            source_app=connector_event.source_app,
            event_name=connector_event.event_type,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "event_type": connector_event.event_type,
            "evidence": evidence.as_dict(),
            "next_step": "Run graph-drain for this session to create the cleaned window and GraphDelta.",
        }

    def post_ack_reply(self, *, channel: str, thread_ts: str = "") -> dict[str, Any]:
        text = "AMO captured this mention locally. I will answer only on tagged Slack messages."
        return self.client.post_message(channel=channel, text=text, thread_ts=thread_ts)

    def post_answer_reply(self, *, message: SlackMessage, search_result: dict[str, Any] | None = None) -> dict[str, Any]:
        result = search_result if search_result is not None else self._search_graph_for_message(message)
        text = build_slack_answer_text(
            query=slack_query_from_text(message.text, self.config.bot_user_id),
            search_result=result,
        )
        return self.client.post_message(channel=message.channel_id, text=text, thread_ts=message.thread_ts or message.ts)

    def _search_graph_for_message(self, message: SlackMessage) -> dict[str, Any]:
        query = slack_query_from_text(message.text, self.config.bot_user_id)
        if not query:
            return {"ok": False, "error": "empty_mention_query"}
        from ....graph.service import GraphRagService

        graph = GraphRagService(self.settings)
        try:
            return graph.graph_search(query=query, limit=6, include_historical=True)
        except Exception as exc:  # Slack should receive a safe failure instead of dropping the mention.
            return {"ok": False, "error": str(exc)}
        finally:
            graph.close()


def config_from_args(
    base: SlackConfig,
    *,
    team_id: str = "",
    bot_user_id: str = "",
    capture_user_ids: list[str] | None = None,
    allowed_channels: list[str] | None = None,
    session_idle_minutes: int | None = None,
) -> SlackConfig:
    return replace(
        base,
        team_id=team_id or base.team_id,
        bot_user_id=bot_user_id or base.bot_user_id,
        capture_user_ids=tuple(capture_user_ids if capture_user_ids is not None else base.capture_user_ids),
        allowed_channels=tuple(allowed_channels if allowed_channels is not None else base.allowed_channels),
        session_idle_minutes=max(1, int(session_idle_minutes or base.session_idle_minutes)),
    )


def load_event_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SlackConnectorError("Slack event file must contain a JSON object")
    return payload


def slack_query_from_text(text: str, bot_user_id: str) -> str:
    raw = str(text or "")
    if bot_user_id:
        raw = re.sub(rf"<@{re.escape(bot_user_id)}>\s*", "", raw)
    return re.sub(r"\s+", " ", raw).strip()


def build_slack_answer_text(*, query: str, search_result: dict[str, Any]) -> str:
    if not query:
        return "Ask a question after tagging AMO, for example: `<@AMO> why was session_graph changed?`"
    if not search_result.get("ok"):
        error = str(search_result.get("error") or "unknown error")
        return _slack_trim(f"AMO captured the mention, but could not query graph memory for: {query}\n\nError: {error}")

    context = str(search_result.get("context") or "").strip()
    nodes = search_result.get("nodes") if isinstance(search_result.get("nodes"), list) else []
    if not nodes:
        return _slack_trim(
            "AMO captured the mention, but no answer-grade graph memory matched this question yet.\n\n"
            f"Query: {query}\n"
            "Next: drain/finalize the relevant session or ask with `raw evidence` if you need provenance records."
        )

    refs = []
    for node in nodes[:3]:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or node.get("node_id") or "")
        evidence_id = str(node.get("evidence_id") or "")
        commit_id = str(node.get("commit_id") or "")
        parts = [part for part in [f"node={node_id}" if node_id else "", f"evidence={evidence_id}" if evidence_id else "", f"commit={commit_id[:12]}" if commit_id else ""] if part]
        if parts:
            refs.append("; ".join(parts))
    refs_text = "\n".join(f"- {ref}" for ref in refs)
    body = context or "AMO found graph memory, but the compressed context was empty."
    if refs_text:
        body = f"{body}\n\nRefs:\n{refs_text}"
    return _slack_trim(f"AMO memory answer for: {query}\n\n{body}")


def _safe_auth_test(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "team": payload.get("team"),
        "team_id": payload.get("team_id"),
        "user_id": payload.get("user_id"),
        "bot_id": payload.get("bot_id"),
        "url": payload.get("url"),
    }


def _slack_trim(text: str, limit: int = SLACK_REPLY_CHAR_LIMIT) -> str:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 22)].rstrip() + "\n...[truncated]"
