from __future__ import annotations

import os
import subprocess
import sys
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


def test_runtime_cli_module_runs_as_python_m_entrypoint() -> None:
    env = os.environ.copy()
    src_path = str(Path("src").resolve())
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_memory_orchestrator.runtime.cli.main",
            "peer-agent",
            "--help",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "peer-agent" in result.stdout
    assert "Drain peer inbox and respond/finalize rooms" in result.stdout
