from __future__ import annotations

import subprocess
from pathlib import Path



def git_unified_zero_diff(repo_root: Path, commit: str, *, file_path: str = "") -> str:
    command = ["git", "show", "--unified=0", "--format=", commit]
    if file_path:
        command.extend(["--", file_path])
    result = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git show failed for {commit}")
    return result.stdout


def git_file_at_commit(repo_root: Path, commit: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{file_path}"],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


__all__ = ["git_file_at_commit", "git_unified_zero_diff"]
