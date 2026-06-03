"""Slack connector infrastructure adapters."""

from __future__ import annotations

from .client import SlackApiClient
from .client import SlackApiError
from .formatter import build_slack_answer_text
from .formatter import slack_query_from_text
from .socket_mode import SlackSocketModeRunner

__all__ = [
    "SlackApiClient",
    "SlackApiError",
    "SlackSocketModeRunner",
    "build_slack_answer_text",
    "slack_query_from_text",
]
