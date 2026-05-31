"""Slack response formatting adapter."""

from __future__ import annotations

from ...integrations.connectors.slack.service import build_slack_answer_text
from ...integrations.connectors.slack.service import slack_query_from_text

__all__ = ["build_slack_answer_text", "slack_query_from_text"]
