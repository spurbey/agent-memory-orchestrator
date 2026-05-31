from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_memory_orchestrator.domain.versioning.repo_resolution import resolve_session_repo_root


def test_repo_resolution_prefers_nested_git_root_that_owns_commits(tmp_path: Path) -> None:
    parent = tmp_path / "Dora"
    nested = parent / "agent-memory-orchestrator"
    parent.mkdir()
    nested.mkdir()
    _git(parent, "init")
    _git(parent, "config", "user.name", "test")
    _git(parent, "config", "user.email", "test@example.com")
    (parent / "dora.txt").write_text("dora\n", encoding="utf-8")
    _git(parent, "add", "dora.txt")
    _git(parent, "commit", "-m", "parent commit")

    _git(nested, "init")
    _git(nested, "config", "user.name", "test")
    _git(nested, "config", "user.email", "test@example.com")
    (nested / "README.md").write_text("amo\n", encoding="utf-8")
    _git(nested, "add", "README.md")
    commit_output = _git(nested, "commit", "-m", "bootstrap amo").stdout
    short_sha = _git(nested, "rev-parse", "--short", "HEAD").stdout.strip()

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "arguments": json.dumps({"command": "git commit -m bootstrap", "workdir": str(nested)}),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "output": commit_output or f"[main {short_sha}] bootstrap amo",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    records = [
        {
            "payload": {
                "cwd": str(parent),
                "transcript_path": str(transcript),
            }
        }
    ]

    resolved = resolve_session_repo_root(records, fallback_repo_path=parent)

    assert Path(resolved.repo_root).resolve() == nested.resolve()
    assert resolved.source == "commit_resolution"
    assert resolved.resolved_commit_count == 1
    assert short_sha in resolved.commit_ids_sample


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result

