from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ....domain.pipeline.constants import PIPELINE_VERSION
from ....domain.pipeline.constants import PRODUCTION_STAGES


JOB_STATUSES = frozenset({"pending", "running", "pending_model", "failed", "complete"})
STAGE_STATUSES = frozenset({"pending", "running", "skipped", "pending_model", "failed", "complete"})


@dataclass(slots=True, frozen=True)
class EnqueueResult:
    job: dict[str, Any]
    created: bool
    updated: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"job": self.job, "created": self.created, "updated": self.updated, "reason": self.reason}


def stable_job_id(session_id: str, pipeline_version: str = PIPELINE_VERSION) -> str:
    digest = hashlib.sha256(f"{pipeline_version}|{session_id}".encode("utf-8")).hexdigest()[:32]
    return f"v2job:{digest}"


def default_artifact_dir(home: Path, pipeline_version: str, session_id: str, job_id: str) -> Path:
    return home / ".state" / "production-jobs" / safe_part(pipeline_version) / f"{safe_part(session_id)[:80]}-{job_id.rsplit(':', 1)[-1][:8]}" / job_id.rsplit(":", 1)[-1]


def safe_part(value: str) -> str:
    out = []
    for ch in str(value):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "value"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if key.endswith("_json"):
            try:
                out[key.removesuffix("_json")] = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                out[key.removesuffix("_json")] = {}
    return out


def _next_stage(stage: str) -> str:
    try:
        index = PRODUCTION_STAGES.index(stage)
    except ValueError:
        return ""
    if index + 1 >= len(PRODUCTION_STAGES):
        return ""
    return PRODUCTION_STAGES[index + 1]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


__all__ = [
    "EnqueueResult",
    "JOB_STATUSES",
    "STAGE_STATUSES",
    "_dedupe",
    "_next_stage",
    "_row",
    "default_artifact_dir",
    "safe_part",
    "stable_job_id",
    "utc_now",
]
