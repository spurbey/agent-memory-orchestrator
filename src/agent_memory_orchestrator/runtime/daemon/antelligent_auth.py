from __future__ import annotations

import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...core.config import Settings

TOKEN_BYTES = 32


def antelligent_token_path(settings: Settings) -> Path:
    return settings.home / ".ui" / "antelligent.token"


def ensure_antelligent_token(settings: Settings) -> str:
    path = antelligent_token_path(settings)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    return token


def antelligent_auth_ok(
    settings: Settings,
    *,
    headers: Mapping[str, Any],
    query: dict[str, list[str]] | None = None,
) -> bool:
    expected = ensure_antelligent_token(settings)
    provided = _bearer_token(headers)
    if not provided and query:
        provided = (query.get("token") or [""])[0]
    return bool(provided and secrets.compare_digest(provided, expected))


def antelligent_auth_error() -> dict[str, Any]:
    return {"ok": False, "error": "antelligent_auth_required"}


def _bearer_token(headers: Mapping[str, Any]) -> str:
    value = str(headers.get("Authorization") or headers.get("authorization") or "").strip()
    prefix = "Bearer "
    if not value.startswith(prefix):
        return ""
    return value[len(prefix) :].strip()


__all__ = [
    "antelligent_auth_error",
    "antelligent_auth_ok",
    "antelligent_token_path",
    "ensure_antelligent_token",
]
