from __future__ import annotations


def test_connector_domain_connector_boundary_exports_existing_contracts() -> None:
    from agent_memory_orchestrator.domain.connectors import ConnectorEvent
    from agent_memory_orchestrator.domain.connectors import ConnectorResponse
    from agent_memory_orchestrator.extensions.contracts.connector import ConnectorResponse as ExtensionConnectorResponse
    from agent_memory_orchestrator.integrations.connectors.base import ConnectorEvent as IntegrationConnectorEvent

    assert ConnectorEvent is IntegrationConnectorEvent
    assert ConnectorResponse is ExtensionConnectorResponse


def test_connector_domain_does_not_export_source_specific_slack_contracts() -> None:
    import agent_memory_orchestrator.domain.connectors as connector_domain

    assert not hasattr(connector_domain, "SlackMessage")
    assert not hasattr(connector_domain, "parse_message_envelope")
    assert not hasattr(connector_domain, "should_reply_message")


def test_connector_application_and_slack_infrastructure_boundaries_are_importable() -> None:
    from agent_memory_orchestrator.application.services import ConnectorRuntimeService
    from agent_memory_orchestrator.infrastructure.slack import SlackApiClient
    from agent_memory_orchestrator.infrastructure.slack import SlackApiError
    from agent_memory_orchestrator.infrastructure.slack import SlackSocketModeRunner
    from agent_memory_orchestrator.infrastructure.slack import build_slack_answer_text
    from agent_memory_orchestrator.infrastructure.slack import slack_query_from_text
    from agent_memory_orchestrator.integrations.connectors.slack.client import SlackApiClient as IntegrationSlackApiClient
    from agent_memory_orchestrator.integrations.connectors.slack.client import SlackApiError as IntegrationSlackApiError
    from agent_memory_orchestrator.integrations.connectors.slack.service import build_slack_answer_text as integration_format
    from agent_memory_orchestrator.integrations.connectors.slack.service import slack_query_from_text as integration_query
    from agent_memory_orchestrator.integrations.connectors.slack.socket_mode import SlackSocketModeRunner as IntegrationRunner

    assert ConnectorRuntimeService.__name__ == "ConnectorRuntimeService"
    assert SlackApiClient is IntegrationSlackApiClient
    assert SlackApiError is IntegrationSlackApiError
    assert SlackSocketModeRunner is IntegrationRunner
    assert build_slack_answer_text is integration_format
    assert slack_query_from_text is integration_query
