from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.app.cli import main
from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.install_service import InstallOptions
from agent_memory_orchestrator.install_service import apply_install_plan
from agent_memory_orchestrator.install_service import build_install_plan
from agent_memory_orchestrator.install_service import doctor
from agent_memory_orchestrator.install_service import uninstall


def test_codex_install_applies_managed_hooks_and_mcp(tmp_path: Path) -> None:
    user_home = tmp_path / "home"
    amo_home = tmp_path / "amo"
    config = user_home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[features]\nexisting = true\n", encoding="utf-8")

    plan = build_install_plan(
        InstallOptions(target="codex", user_home=user_home, amo_home=amo_home, python_command="python")
    )
    result = apply_install_plan(plan)

    text = config.read_text(encoding="utf-8")
    assert result["results"][0]["target"] == "amo"
    assert "existing = true" in text
    assert "codex_hooks = true" in text
    assert "[mcp_servers.agent_memory_orchestrator]" in text
    assert "agent_memory_orchestrator.mcp.server" in text
    assert "[[hooks.UserPromptSubmit]]" not in text
    assert "agent_memory_orchestrator.hook --agent codex" not in text
    assert str(amo_home.resolve()).replace("\\", "\\\\") in text
    assert (amo_home / "bin" / "amo_hook_launcher.py").exists()

    hooks_payload = json.loads((user_home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "UserPromptSubmit" in hooks_payload["hooks"]
    assert "PostToolUse" in hooks_payload["hooks"]
    hook_command = hooks_payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "amo_hook_launcher.py" in hook_command
    assert "--agent codex" in hook_command
    assert str(amo_home.resolve()) in hook_command

    status = doctor(target="codex", user_home=user_home, amo_home=amo_home)
    assert status["ok"] is True
    assert status["checks"]["codex"]["hooks_configured"] is True
    assert status["checks"]["codex"]["hooks_file_exists"] is True


def test_claude_install_merges_json_settings(tmp_path: Path) -> None:
    user_home = tmp_path / "home"
    amo_home = tmp_path / "amo"
    settings = user_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "dark", "hooks": {"Stop": []}}), encoding="utf-8")

    plan = build_install_plan(
        InstallOptions(target="claude", user_home=user_home, amo_home=amo_home, python_command="python")
    )
    apply_install_plan(plan)

    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert "agent-memory-orchestrator" in payload["mcpServers"]
    assert payload["mcpServers"]["agent-memory-orchestrator"]["args"][-1] == str(amo_home.resolve())
    assert "UserPromptSubmit" in payload["hooks"]
    assert any("agent_memory_orchestrator.hook --agent claude" in hook["command"] for hook in payload["hooks"]["Stop"][0]["hooks"])

    status = doctor(target="claude", user_home=user_home, amo_home=amo_home)
    assert status["checks"]["claude"]["mcp_configured"] is True
    assert status["checks"]["claude"]["hooks_configured"] is True


def test_uninstall_removes_managed_entries(tmp_path: Path) -> None:
    user_home = tmp_path / "home"
    amo_home = tmp_path / "amo"
    plan = build_install_plan(InstallOptions(target="all", user_home=user_home, amo_home=amo_home))
    apply_install_plan(plan)

    result = uninstall(target="all", user_home=user_home)

    assert result["ok"] is True
    codex_text = (user_home / ".codex" / "config.toml").read_text(encoding="utf-8")
    codex_hooks = (user_home / ".codex" / "hooks.json").read_text(encoding="utf-8")
    claude_payload = json.loads((user_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "agent_memory_orchestrator.hook" not in codex_text
    assert "agent_memory_orchestrator.hook" not in codex_hooks
    assert "amo_hook_launcher.py" not in codex_hooks
    assert "agent-memory-orchestrator" not in claude_payload.get("mcpServers", {})
    assert not any(claude_payload.get("hooks", {}).values())


def test_settings_loads_installer_runtime_config(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "home"
    amo_home = tmp_path / "amo"
    plan = build_install_plan(InstallOptions(target="codex", user_home=user_home, amo_home=amo_home, preset="cpu-light"))
    apply_install_plan(plan)

    monkeypatch.setenv("AMO_HOME", str(amo_home))
    monkeypatch.delenv("AMO_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("AMO_RERANKER_MODEL", raising=False)
    monkeypatch.delenv("AMO_RERANKER_BACKEND", raising=False)
    settings = Settings.load()

    assert settings.embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.db_path == amo_home.resolve() / ".data" / "codex_live_memory.db"
    assert settings.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert settings.reranker_backend == "cross-encoder"
    assert settings.vector_backend == "faiss"
    assert settings.qwen_model == "qwen3:1.7b"
    assert settings.qwen_timeout_seconds == 20.0
    assert settings.qwen_planner_timeout_seconds == 8.0
    assert settings.qwen_extract_timeout_seconds == 25.0
    assert settings.qwen_compress_timeout_seconds == 12.0
    assert settings.qwen_num_ctx == 2048
    assert settings.drain_max_windows_per_run == 3


def test_settings_loads_bom_prefixed_json_config(tmp_path: Path, monkeypatch) -> None:
    amo_home = tmp_path / "amo"
    amo_home.mkdir()
    (amo_home / "config.json").write_text(
        "\ufeff" + json.dumps({"mcp_port": 18765, "qwen_model": "qwen3:1.7b", "qwen_timeout_seconds": 7}),
        encoding="utf-8",
    )

    monkeypatch.setenv("AMO_HOME", str(amo_home))
    settings = Settings.load()

    assert settings.mcp_port == 18765
    assert settings.qwen_model == "qwen3:1.7b"
    assert settings.qwen_timeout_seconds == 7.0
    assert settings.qwen_planner_timeout_seconds == 8.0


def test_cli_install_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    user_home = tmp_path / "home"
    amo_home = tmp_path / "amo"

    exit_code = main(
        [
            "install",
            "--target",
            "codex",
            "--user-home",
            str(user_home),
            "--amo-home",
            str(amo_home),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["dry_run"] is True
    assert not (user_home / ".codex" / "config.toml").exists()
