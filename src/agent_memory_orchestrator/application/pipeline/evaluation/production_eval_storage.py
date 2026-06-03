from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def _job_row(db_path: Path, job_id: str) -> dict[str, Any]:
    rows = _query(db_path, "SELECT * FROM v2_session_jobs WHERE job_id = ?", (job_id,))
    return rows[0] if rows else {}


def _stage_rows(db_path: Path, job_id: str) -> list[dict[str, Any]]:
    return _query(db_path, "SELECT * FROM v2_session_job_stages WHERE job_id = ? ORDER BY started_at ASC", (job_id,))


def _stage_json(stage: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(stage.get("output_artifact") or ""))
    payload = _read_json(path)
    if isinstance(payload, dict) and payload:
        return payload
    diagnostics = stage.get("diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {}

def _query(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    uri = db_path.resolve().as_posix().replace("'", "''")
    conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_decode_row(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _scalar(db_path: Path, sql: str, params: tuple[Any, ...] = (), *, default: int = 0) -> int:
    rows = _query(db_path, sql, params)
    if not rows:
        return default
    first = next(iter(rows[0].values()), default)
    try:
        return int(first)
    except (TypeError, ValueError):
        return default


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, str) and key.endswith("_json"):
            try:
                out[key.removesuffix("_json")] = json.loads(value)
            except json.JSONDecodeError:
                out[key.removesuffix("_json")] = {}
    return out


def _read_json(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _select(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys}


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
