from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return path_hash(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def path_hash(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    if path.is_dir():
        rows: list[str] = []
        for child in sorted(path.rglob("*")):
            if child.is_file():
                rows.append(f"{child.relative_to(path)}:{hashlib.sha256(child.read_bytes()).hexdigest()}")
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return ""


__all__ = ["file_sha256", "path_hash"]
