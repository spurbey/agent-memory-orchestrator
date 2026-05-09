from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .config import validate_token_prefixes


SLACK_API = "https://slack.com/api"
JsonTransport = Callable[[str, str, dict[str, Any], float], dict[str, Any]]


class SlackApiError(RuntimeError):
    pass


class SlackApiClient:
    def __init__(
        self,
        *,
        app_token: str = "",
        bot_token: str = "",
        timeout_seconds: float = 10.0,
        transport: JsonTransport | None = None,
    ) -> None:
        self.app_token = app_token
        self.bot_token = bot_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _urllib_post_json

    def validate_prefixes(self) -> dict[str, Any]:
        return validate_token_prefixes(self.app_token, self.bot_token)

    def auth_test(self) -> dict[str, Any]:
        return self._bot_post("auth.test", {})

    def open_socket(self) -> str:
        payload = self._app_post("apps.connections.open", {})
        url = str(payload.get("url") or "")
        if not url:
            raise SlackApiError("Slack apps.connections.open response did not include a websocket URL")
        return url

    def create_app_from_manifest(
        self,
        *,
        config_token: str,
        manifest: dict[str, Any],
        team_id: str = "",
    ) -> dict[str, Any]:
        if not config_token:
            raise SlackApiError("Slack app configuration token is required")
        payload: dict[str, Any] = {"manifest": json.dumps(manifest, ensure_ascii=False, sort_keys=True)}
        if team_id:
            payload["team_id"] = team_id
        return self._post("apps.manifest.create", config_token, payload)

    def post_message(self, *, channel: str, text: str, thread_ts: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return self._bot_post("chat.postMessage", payload)

    def _app_post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.app_token:
            raise SlackApiError("Slack app token is required for Socket Mode")
        return self._post(method, self.app_token, payload)

    def _bot_post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.bot_token:
            raise SlackApiError("Slack bot token is required")
        return self._post(method, self.bot_token, payload)

    def _post(self, method: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.transport(f"{SLACK_API}/{method}", token, payload, self.timeout_seconds)
        if not result.get("ok"):
            raise SlackApiError(f"slack_api_error:{method}:{result.get('error') or 'unknown'}")
        return result


def _urllib_post_json(url: str, token: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - configured Slack API URL.
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise SlackApiError(f"slack_network_error:{exc}") from exc
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise SlackApiError("slack_api_error:non_object_response")
    return parsed
