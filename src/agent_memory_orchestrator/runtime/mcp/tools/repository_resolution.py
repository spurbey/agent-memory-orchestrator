from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ....core.config import Settings


def resolve_active_repo_id(settings: Settings, repo_id: str) -> str:
    """Resolve user-facing repo names to canonical active projection ids."""
    requested = str(repo_id or "").strip()
    if requested.startswith("repo:"):
        return requested
    return _active_repo_id_for_alias(settings, requested) or _default_active_repo_id(settings)


def _default_active_repo_id(settings: Settings) -> str:
    path = _retrieval_db_path(settings)
    if not path.exists():
        return ""
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT active_retrieval_projection.repo_id
                FROM active_retrieval_projection
                JOIN retrieval_projections
                  ON retrieval_projections.projection_id = active_retrieval_projection.projection_id
                WHERE active_retrieval_projection.repo_id != ''
                  AND retrieval_projections.status = 'active'
                ORDER BY active_retrieval_projection.updated_at DESC,
                         active_retrieval_projection.repo_id ASC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row["repo_id"] or "") if row else ""


def _active_repo_id_for_alias(settings: Settings, alias: str) -> str:
    safe_alias = _normalize_repo_alias(alias)
    if not safe_alias:
        return ""
    path = _retrieval_db_path(settings)
    if not path.exists():
        return ""
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT active_retrieval_projection.repo_id,
                       active_retrieval_projection.projection_id
                FROM active_retrieval_projection
                JOIN retrieval_projections
                  ON retrieval_projections.projection_id = active_retrieval_projection.projection_id
                WHERE active_retrieval_projection.repo_id != ''
                  AND retrieval_projections.status = 'active'
                ORDER BY active_retrieval_projection.updated_at DESC,
                         active_retrieval_projection.repo_id ASC
                """
            ).fetchall()
            for row in rows:
                candidate_repo_id = str(row["repo_id"] or "")
                aliases = _repo_aliases_for_projection(
                    conn=conn,
                    repo_id=candidate_repo_id,
                    projection_id=str(row["projection_id"] or ""),
                )
                if safe_alias in aliases:
                    return candidate_repo_id
    except sqlite3.Error:
        return ""
    return ""


def _retrieval_db_path(settings: Settings) -> Path:
    path = settings.retrieval_db_path
    return path if path.is_absolute() else (settings.home / path).resolve()


def _normalize_repo_alias(value: str) -> str:
    text = str(value or "").strip().lower().replace("\\", "/")
    text = re.sub(r"[^a-z0-9._:/-]+", "-", text)
    text = text.strip("-/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text


def _repo_aliases_for_projection(*, conn: sqlite3.Connection, repo_id: str, projection_id: str) -> set[str]:
    aliases = {_normalize_repo_alias(repo_id)}
    rows = conn.execute(
        """
        SELECT metadata_json
        FROM retrieval_documents
        WHERE repo_id = ?
          AND projection_id = ?
          AND metadata_json LIKE '%repo_path%'
        LIMIT 25
        """,
        (repo_id, projection_id),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        repo_path = str(metadata.get("repo_path") or "").strip()
        if repo_path:
            aliases.add(_normalize_repo_alias(repo_path))
            aliases.add(_normalize_repo_alias(Path(repo_path).name))
    return {alias for alias in aliases if alias}
