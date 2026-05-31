from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from agent_memory_orchestrator.runtime.hook import launcher as hook


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


class _JsonStdin:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return json.dumps(self.payload)


class _BlockingStdin:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        self.entered.set()
        self.release.wait(timeout=10)
        return ""


def test_hook_manual_smoke_mode_does_not_read_stdin(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _InteractiveStdin())

    assert hook.main(["--amo-home", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["manualSmoke"] is True
    assert payload["captureOnly"] is True
    assert payload["ingested"] is True
    assert payload["evidence"]["id"].startswith("raw_")
    assert "hookSpecificOutput" not in payload
    assert (tmp_path / ".evidence").exists()


def test_hook_query_forces_manual_smoke_even_without_tty(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())

    assert hook.main(["--amo-home", str(tmp_path), "--query", "what did we decide about codex hooks"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["manualSmoke"] is True
    assert payload["captureOnly"] is True
    assert payload["ingested"] is True
    assert payload["event_type"] == "user_prompt_submit"


def test_hook_captures_json_payload_from_stdin(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        _JsonStdin(
            {
                "session_id": "codex-real-s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "AMO_HOOK_PROBE",
                "cwd": str(tmp_path),
            }
        ),
    )

    assert hook.main(["--amo-home", str(tmp_path)]) == 0

    assert capsys.readouterr().out == ""
    assert list((tmp_path / ".evidence").glob("*.jsonl"))


def test_hook_returns_quickly_when_stdin_never_closes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    stdin = _BlockingStdin()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setenv("AMO_HOOK_STDIN_TIMEOUT_MS", "50")

    assert hook.main(["--amo-home", str(tmp_path)]) == 0
    stdin.release.set()

    assert capsys.readouterr().out == ""
    assert (tmp_path / "logs" / "hook.log").exists()


def test_session_start_is_the_only_hook_that_injects_startup_context(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())

    assert hook.main(["--amo-home", str(tmp_path), "--query", "start", "--event-name", "SessionStart"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["captureOnly"] is True
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "amo_graph_search" in payload["hookSpecificOutput"]["additionalContext"]


def test_hook_falls_back_to_workspace_spool_when_amo_home_is_not_writable(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.chdir(tmp_path)

    original_store = hook.RawEvidenceStore

    class _PermissionThenStore:
        calls = 0

        def __init__(self, root: Path) -> None:
            self.root = root

        def append(self, *args, **kwargs):
            type(self).calls += 1
            if type(self).calls == 1:
                raise PermissionError("sandbox blocked central AMO home")
            return original_store(self.root).append(*args, **kwargs)

    monkeypatch.setattr(hook, "RawEvidenceStore", _PermissionThenStore)

    assert hook.main(["--amo-home", str(tmp_path / "amo"), "--query", "fallback spool"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["continue"] is True
    assert payload["ingested"] is True
    assert payload["fallback_spool"] is True
    assert ".amo-spool" in payload["evidence"]["path"]

