from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class RepoIdentity:
    repo_id: str
    repo_path: str
    source: str
    normalized_remote: str = ""
    git_root: str = ""
    diagnostics: dict[str, str] | None = None

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["diagnostics"] = self.diagnostics or {}
        return payload


def resolve_repo_identity(repo_path: str | Path, *, configured_repo_id: str = "") -> RepoIdentity:
    path = Path(str(repo_path or ".")).expanduser()
    resolved = path.resolve() if path.exists() else path.absolute()
    configured = str(configured_repo_id or "").strip()
    if configured:
        return RepoIdentity(repo_id=f"repo:configured:{configured}", repo_path=str(resolved), source="configured")

    root = _git_root(resolved)
    remote = _git_remote(root or resolved)
    if remote:
        normalized = normalize_remote_url(remote)
        return RepoIdentity(
            repo_id=f"repo:remote:{_short_hash(normalized)}",
            repo_path=str(resolved),
            source="git_remote",
            normalized_remote=normalized,
            git_root=str(root or ""),
        )

    basis = str(root or resolved).lower()
    return RepoIdentity(
        repo_id=f"repo:local:{_short_hash(basis)}",
        repo_path=str(resolved),
        source="git_root" if root else "local_path",
        git_root=str(root or ""),
        diagnostics={"fallback_basis": basis},
    )


def normalize_remote_url(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^git@([^:]+):(.+)$", r"https://\1/\2", text)
    text = re.sub(r"^ssh://git@([^/]+)/(.+)$", r"https://\1/\2", text)
    text = text.replace("\\", "/")
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    match = re.match(r"^(https?://)([^/]+)(/.*)$", text, flags=re.IGNORECASE)
    if match:
        text = f"{match.group(1).lower()}{match.group(2).lower()}{match.group(3)}"
    return text


def _git_root(path: Path) -> Path | None:
    result = _git(path, "rev-parse", "--show-toplevel")
    if not result:
        return None
    root = Path(result)
    return root.resolve() if root.exists() else root


def _git_remote(path: Path) -> str:
    return _git(path, "remote", "get-url", "origin")


def _git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
