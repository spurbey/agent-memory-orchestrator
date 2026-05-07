from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_memory_orchestrator import hook


class _InteractiveStdin:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:
        raise AssertionError("manual hook smoke mode must not block on stdin.read()")


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
    assert payload["ingested"] is False
    assert "hookSpecificOutput" not in payload
