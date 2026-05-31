from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


def timestamped_backup_path(path: Path, *, label: str = "backup", timestamp: str = "") -> Path:
    safe_timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.{label}-{safe_timestamp}")


def backup_copy(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return target


__all__ = ["backup_copy", "timestamped_backup_path"]
