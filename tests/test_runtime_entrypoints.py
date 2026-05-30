from __future__ import annotations

from pathlib import Path


def test_console_scripts_point_to_runtime_adapters() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'amo-cli = "agent_memory_orchestrator.runtime.cli.main:main"' in pyproject
    assert 'amo-mcp = "agent_memory_orchestrator.runtime.mcp.server:main"' in pyproject
    assert 'amo-daemon = "agent_memory_orchestrator.runtime.daemon.server:main"' in pyproject
    assert 'amo-hook = "agent_memory_orchestrator.runtime.hook.launcher:main"' in pyproject


def test_runtime_adapters_keep_compatibility_imports() -> None:
    from agent_memory_orchestrator import cli as compat_cli
    from agent_memory_orchestrator import daemon_client as compat_daemon_client
    from agent_memory_orchestrator.app import cli as app_cli
    from agent_memory_orchestrator.app import client as app_client
    from agent_memory_orchestrator.app import daemon as app_daemon
    from agent_memory_orchestrator.app import hook as app_hook
    from agent_memory_orchestrator.mcp import server as mcp_server
    from agent_memory_orchestrator.mcp import tools as mcp_tools
    from agent_memory_orchestrator.runtime.cli import main as runtime_cli
    from agent_memory_orchestrator.runtime.daemon import client as runtime_daemon_client
    from agent_memory_orchestrator.runtime.daemon import server as runtime_daemon
    from agent_memory_orchestrator.runtime.hook import launcher as runtime_hook
    from agent_memory_orchestrator.runtime.mcp import server as runtime_mcp
    from agent_memory_orchestrator.runtime.mcp import tools as runtime_mcp_tools

    assert compat_cli is runtime_cli
    assert app_cli is runtime_cli
    assert app_client is runtime_daemon_client
    assert compat_daemon_client.DaemonClient is runtime_daemon_client.DaemonClient
    assert runtime_daemon.main is app_daemon.main
    assert runtime_hook.main is app_hook.main
    assert runtime_mcp.main is mcp_server.main
    assert runtime_mcp.create_server is mcp_server.create_server
    assert runtime_mcp_tools.MemoryMcpToolService is mcp_tools.MemoryMcpToolService
