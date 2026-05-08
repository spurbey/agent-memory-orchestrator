from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_memory_orchestrator import hook


class _FakeGraphService:
    def __init__(self, settings) -> None:
        self.settings = settings

    def capture_hook(self, payload, *, default_agent: str = "codex"):
        return {
            "ok": True,
            "session_id": payload.get("session_id") or "manual-smoke",
            "event_type": "session_start" if payload.get("hook_event_name") == "SessionStart" else "user_prompt_submit",
            "evidence": {"id": "raw_test"},
            "merge": {"merged": False},
            "additional_context": "startup graph context" if payload.get("hook_event_name") == "SessionStart" else "",
        }

    def close(self) -> None:
        return


class _InteractiveStdin:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:
        raise AssertionError("manual hook smoke mode must not block on stdin.read()")


class _NonInteractiveStdin:
    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        raise AssertionError("--query smoke mode must not block on stdin.read()")


def test_hook_manual_smoke_mode_does_not_read_stdin(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _InteractiveStdin())
    monkeypatch.setattr(hook, "GraphRagService", _FakeGraphService)

    assert hook.main(["--amo-home", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["manualSmoke"] is True
    assert payload["captureOnly"] is True
    assert payload["ingested"] is True
    assert "hookSpecificOutput" not in payload


def test_hook_query_forces_manual_smoke_even_without_tty(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.setattr(hook, "GraphRagService", _FakeGraphService)

    assert hook.main(["--amo-home", str(tmp_path), "--query", "what did we decide about codex hooks"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["manualSmoke"] is True
    assert payload["captureOnly"] is True
    assert payload["ingested"] is True


def test_session_start_is_the_only_hook_that_injects_startup_context(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.setattr(hook, "GraphRagService", _FakeGraphService)

    assert hook.main(["--amo-home", str(tmp_path), "--query", "start", "--event-name", "SessionStart"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["captureOnly"] is True
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "amo_graph_search" in payload["hookSpecificOutput"]["additionalContext"] or "startup graph context" in payload["hookSpecificOutput"]["additionalContext"]
