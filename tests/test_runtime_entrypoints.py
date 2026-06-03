from __future__ import annotations

from pathlib import Path


def test_console_scripts_point_to_runtime_adapters() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'amo-cli = "agent_memory_orchestrator.runtime.cli.main:main"' in pyproject
    assert 'amo-mcp = "agent_memory_orchestrator.runtime.mcp.server:main"' in pyproject
    assert 'amo-daemon = "agent_memory_orchestrator.runtime.daemon.server:main"' in pyproject
    assert 'amo-hook = "agent_memory_orchestrator.runtime.hook.launcher:main"' in pyproject


def test_runtime_adapters_expose_canonical_entrypoints() -> None:
    from agent_memory_orchestrator.runtime.cli import main as runtime_cli
    from agent_memory_orchestrator.runtime.daemon import client as runtime_daemon_client
    from agent_memory_orchestrator.runtime.daemon import server as runtime_daemon
    from agent_memory_orchestrator.runtime.hook import launcher as runtime_hook
    from agent_memory_orchestrator.runtime.mcp import server as runtime_mcp
    from agent_memory_orchestrator.runtime.mcp import tools as runtime_mcp_tools

    assert callable(runtime_cli.main)
    assert runtime_daemon_client.DaemonClient.__name__ == "DaemonClient"
    assert callable(runtime_daemon.main)
    assert callable(runtime_hook.main)
    assert callable(runtime_mcp.main)
    assert callable(runtime_mcp.create_server)
    assert runtime_mcp_tools.MemoryMcpToolService.__name__ == "MemoryMcpToolService"
