from __future__ import annotations


def test_cli_boundary_groups_are_importable() -> None:
    from agent_memory_orchestrator.runtime.cli.commands.connectors import SLACK_SUBCOMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.graph import GRAPH_COMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.models import MODEL_SUBCOMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.pipeline import PRODUCTION_SUBCOMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.retrieval import RETRIEVAL_COMMANDS

    assert "merge-apply" in PRODUCTION_SUBCOMMANDS
    assert "graph-retrieve" in RETRIEVAL_COMMANDS
    assert "graph-status" in GRAPH_COMMANDS
    assert "status" in SLACK_SUBCOMMANDS
    assert "preflight" in MODEL_SUBCOMMANDS


def test_daemon_route_boundary_groups_are_importable() -> None:
    from agent_memory_orchestrator.runtime.daemon.routes.connectors import CONNECTOR_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.graph import GRAPH_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.health import HEALTH_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.hooks import HOOK_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.jobs import JOB_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.retrieval import RETRIEVAL_ROUTES

    assert "/health" in HEALTH_ROUTES
    assert "/api/jobs" in JOB_ROUTES
    assert "/api/graph/status" in GRAPH_ROUTES
    assert "/graph/retrieve" in RETRIEVAL_ROUTES
    assert "/api/connectors/slack/status" in CONNECTOR_ROUTES
    assert "/hooks/ingest" in HOOK_ROUTES


def test_mcp_tool_boundary_groups_are_importable() -> None:
    from agent_memory_orchestrator.runtime.mcp.tools.graph import GRAPH_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.memory import MEMORY_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.orchestration import ORCHESTRATION_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.peer import PEER_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.retrieval import RETRIEVAL_TOOL_NAMES

    assert "memory_search" in MEMORY_TOOL_NAMES
    assert "amo_graph_search" in GRAPH_TOOL_NAMES
    assert "amo_graph_search" in RETRIEVAL_TOOL_NAMES
    assert "peer_memory_ask" in PEER_TOOL_NAMES
    assert "orchestrator_start" in ORCHESTRATION_TOOL_NAMES


def test_runtime_web_boundary_delegates_to_existing_asset_loader() -> None:
    from agent_memory_orchestrator.runtime.daemon import web_assets as daemon_assets
    from agent_memory_orchestrator.runtime.web import assets as runtime_assets

    assert runtime_assets.load_web_asset is daemon_assets.load_web_asset
    assert runtime_assets.web_asset_bytes is daemon_assets.web_asset_bytes
    assert "AMO Control Room" in runtime_assets.session_cockpit_html()
