"""Slack Web API infrastructure adapter."""

from __future__ import annotations

from ...integrations.connectors.slack.client import SlackApiClient
from ...integrations.connectors.slack.client import SlackApiError

__all__ = ["SlackApiClient", "SlackApiError"]
