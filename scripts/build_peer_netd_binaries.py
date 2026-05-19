from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


TARGETS = (
    ("windows", "amd64"),
    ("windows", "arm64"),
    ("darwin", "amd64"),
    ("darwin", "arm64"),
    ("linux", "amd64"),
    ("linux", "arm64"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build amo-peer-netd release binaries.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--target", action="append", default=[], help="Target as GOOS/GOARCH, e.g. linux/amd64.")
    parser.add_argument("--go", default="", help="Optional go executable path.")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    peer_netd = repo_root / "peer-netd"
    out_dir = (args.out_dir or repo_root / "src" / "agent_memory_orchestrator" / "bin").resolve()
    go = args.go or shutil.which("go") or str(repo_root / ".tmp" / "tools" / "go" / "bin" / _go_binary_name())
    if not Path(go).exists() and shutil.which(go) is None:
        raise SystemExit(f"go executable not found: {go}")

    targets = [_parse_target(item) for item in args.target] if args.target else list(TARGETS)
    for goos, goarch in targets:
        target_dir = out_dir / f"{goos}-{goarch}"
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / ("amo-peer-netd.exe" if goos == "windows" else "amo-peer-netd")
        env = os.environ.copy()
        env["GOOS"] = goos
        env["GOARCH"] = goarch
        env["CGO_ENABLED"] = "0"
        result = subprocess.run(
            [go, "build", "-trimpath", "-o", str(output), "./cmd/amo-peer-netd"],
            cwd=peer_netd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(result.stderr.strip() or result.stdout.strip() or f"build failed for {goos}/{goarch}")
        print(output)
    return 0


def _parse_target(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError("target must be GOOS/GOARCH")
    return parts[0], parts[1]


def _go_binary_name() -> str:
    return "go.exe" if os.name == "nt" else "go"


if __name__ == "__main__":
    raise SystemExit(main())
