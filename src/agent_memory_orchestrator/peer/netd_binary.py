from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.peer.netd_errors import PeerNetdRuntimeError
from agent_memory_orchestrator.peer.netd_platform import binary_name
from agent_memory_orchestrator.peer.netd_platform import platform_binary_dir_name


def build_binary(*, source_dir: Path, target: Path, go_path: str) -> dict[str, Any]:
    if not source_dir.exists():
        raise PeerNetdRuntimeError(f"peer-netd source directory not found: {source_dir}")
    if not go_path:
        raise PeerNetdRuntimeError("Go toolchain not found; install Go or set PATH before building peer-netd")

    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [go_path, "build", "-o", str(target), ".\\cmd\\amo-peer-netd" if os.name == "nt" else "./cmd/amo-peer-netd"],
        cwd=source_dir,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise PeerNetdRuntimeError(f"go build failed: {result.stderr.strip() or result.stdout.strip()}")
    return {
        "ok": True,
        "binary": str(target),
        "source_dir": str(source_dir),
        "go": go_path,
    }


def binary_capabilities(
    binary: Path,
    *,
    required_flags: tuple[str, ...],
    required_protocol_capabilities: tuple[str, ...],
) -> dict[str, Any]:
    candidate = binary.expanduser().resolve()
    if not candidate.exists():
        return {
            "ok": False,
            "binary": str(candidate),
            "exists": False,
            "missing_required_flags": list(required_flags),
            "missing_protocol_capabilities": list(required_protocol_capabilities),
        }
    try:
        result = subprocess.run(
            [str(candidate), "-h"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "binary": str(candidate),
            "exists": True,
            "executable": False,
            "error": str(exc),
            "missing_required_flags": list(required_flags),
            "missing_protocol_capabilities": list(required_protocol_capabilities),
        }
    help_text = f"{result.stdout}\n{result.stderr}"
    missing = [
        flag
        for flag in required_flags
        if f"-{flag}" not in help_text and f"--{flag}" not in help_text
    ]
    protocol = protocol_capabilities(candidate)
    missing_protocol = [
        capability
        for capability in required_protocol_capabilities
        if capability not in protocol.get("protocol_capabilities", [])
    ]
    return {
        "ok": not missing and not missing_protocol,
        "binary": str(candidate),
        "exists": True,
        "executable": True,
        "returncode": result.returncode,
        "missing_required_flags": missing,
        "protocol_capabilities": protocol.get("protocol_capabilities", []),
        "missing_protocol_capabilities": missing_protocol,
        "capabilities_error": protocol.get("error", ""),
    }


def protocol_capabilities(binary: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(binary), "--capabilities"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "protocol_capabilities": [], "error": str(exc)}
    if result.returncode != 0:
        return {
            "ok": False,
            "protocol_capabilities": [],
            "error": (result.stderr.strip() or result.stdout.strip()),
        }
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        return {"ok": False, "protocol_capabilities": [], "error": str(exc)}
    capabilities = payload.get("protocol_capabilities") if isinstance(payload, dict) else []
    if not isinstance(capabilities, list):
        capabilities = []
    return {"ok": True, "protocol_capabilities": [str(item) for item in capabilities]}


def install_packaged_binary(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    if os.name != "nt":
        target.chmod(target.stat().st_mode | 0o111)
    return target


def packaged_binary_path(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def packaged_binary_candidates(*, repo_root: Path | None) -> list[Path]:
    candidates: list[Path] = []
    installed_package_root = Path(__file__).resolve().parents[1]
    candidates.append(installed_package_root / "bin" / platform_binary_dir_name() / binary_name())
    for source_dir in source_dir_candidates(repo_root=repo_root):
        candidates.append(source_dir / "bin" / platform_binary_dir_name() / binary_name())

    prefix_root = Path(sys.prefix).resolve()
    candidates.extend(
        [
            prefix_root / "bin" / platform_binary_dir_name() / binary_name(),
            prefix_root / "Scripts" / platform_binary_dir_name() / binary_name(),
        ]
    )
    return list(dict.fromkeys(candidates))


def source_dir(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def source_dir_candidates(*, repo_root: Path | None) -> list[Path]:
    if repo_root:
        return [repo_root / "peer-netd"]

    package_root = Path(__file__).resolve().parents[3]
    prefix_root = Path(sys.prefix).resolve()
    candidates = [
        package_root / "peer-netd",
        prefix_root / "peer-netd",
        prefix_root / "Lib" / "peer-netd",
        prefix_root / "share" / "peer-netd",
    ]
    return list(dict.fromkeys(candidates))


def go_path(source_dir: Path) -> str:
    found = shutil.which("go")
    if found:
        return found
    bundled = source_dir.parent / ".tmp" / "tools" / "go" / "bin" / ("go.exe" if os.name == "nt" else "go")
    return str(bundled) if bundled.exists() else ""


def resolve_binary(binary_path: Path | None, *, default_path: Path, packaged_path: Path | None) -> Path:
    if binary_path:
        return binary_path.expanduser().resolve()
    env_path = os.getenv("AMO_PEER_NETD_BIN", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    if default_path.exists():
        return default_path
    found = shutil.which(binary_name())
    if found:
        return Path(found).resolve()
    if packaged_path is not None:
        return packaged_path
    return default_path
