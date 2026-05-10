from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import NormalizedAdapterEvent, compact_json, content_to_text, snake


MAX_CODEX_IMPORT_CONTENT_CHARS = 12000


def normalize_codex_event(
    item: dict,
    *,
    default_agent: str = "system",
    default_session_id: str | None = None,
) -> NormalizedAdapterEvent | None:
    if not _looks_like_codex(item, default_agent):
        return None

    kind = item.get("type")
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return None
    ts = item.get("timestamp") or payload.get("timestamp")
    session_id = default_session_id or str(payload.get("id") or payload.get("session_id") or "codex-session")
    metadata = {"adapter": "codex", "rollout_type": kind}

    if kind == "session_meta":
        session_id = str(payload.get("id") or session_id)
        cwd = payload.get("cwd") or ""
        source = payload.get("source") or payload.get("originator") or "codex"
        model = payload.get("model") or payload.get("model_provider") or ""
        return NormalizedAdapterEvent(
            session_id=session_id,
            agent="system",
            event_type="session_meta",
            content=f"Codex session started. cwd={cwd} source={source} model={model}",
            metadata={
                "adapter": "codex",
                "cwd": cwd,
                "source": source,
                "originator": payload.get("originator"),
                "cli_version": payload.get("cli_version"),
                "forked_from_id": payload.get("forked_from_id"),
            },
            created_at=ts,
            source_app="codex",
        )

    if kind == "turn_context":
        return None

    if kind == "compacted":
        message = str(payload.get("message") or "").strip()
        if not message:
            return None
        return NormalizedAdapterEvent(
            session_id=session_id,
            agent="system",
            event_type="summary",
            content=message,
            metadata=metadata,
            created_at=ts,
            source_app="codex",
        )

    subtype = payload.get("type")
    if kind == "event_msg":
        if subtype == "user_message":
            return _normalized(session_id, "user", "prompt", payload.get("message"), metadata | {"turn_id": payload.get("turn_id")}, ts)
        if subtype == "agent_message":
            return _normalized(session_id, "codex", "response", payload.get("message"), metadata | {"turn_id": payload.get("turn_id")}, ts)
        if subtype in {"exec_command_end", "patch_apply_end", "mcp_tool_call_end"}:
            content = _codex_tool_result_text(payload)
            if not content.strip():
                return None
            return _normalized(session_id, "codex", "tool_result", content, metadata | _small_tool_metadata(payload), ts)
        if subtype in {"task_complete", "turn_aborted", "error"}:
            content = payload.get("message") or payload.get("error") or subtype
            return _normalized(session_id, "system", subtype, content, metadata | {"turn_id": payload.get("turn_id")}, ts)
        return None

    if kind == "response_item":
        # Codex rollout files commonly contain both response_item records and
        # event_msg records for the same activity. Historical import prefers
        # event_msg to avoid duplicate memories and oversized imports.
        return None

    return None


def infer_codex_session(file_path: Path) -> tuple[str, str]:
    try:
        with file_path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
                    payload = item["payload"]
                    sid = str(payload.get("id") or file_path.stem)
                    cwd = payload.get("cwd") or ""
                    return sid, f"Codex {sid} {cwd}".strip()
                break
    except Exception:
        pass
    return file_path.stem, f"Codex {file_path.stem}"


def _looks_like_codex(item: dict, default_agent: str) -> bool:
    if default_agent == "codex":
        return item.get("type") in {"session_meta", "event_msg", "response_item", "turn_context", "compacted"}
    if item.get("source_app") == "codex" or item.get("agent") == "codex":
        return item.get("type") in {"session_meta", "event_msg", "response_item", "turn_context", "compacted"}
    return False


def _normalized(
    session_id: str,
    agent: str,
    event_type: str,
    content: object,
    metadata: dict[str, Any],
    created_at: object,
) -> NormalizedAdapterEvent | None:
    text = content_to_text(content)
    if not text.strip():
        return None
    return NormalizedAdapterEvent(
        session_id=session_id,
        agent=agent,
        event_type=snake(event_type),
        content=text,
        metadata=metadata,
        created_at=created_at,
        source_app="codex",
    )


def _codex_tool_result_text(payload: dict) -> str:
    subtype = payload.get("type")
    if subtype == "exec_command_end":
        command = payload.get("command") or ""
        output = payload.get("aggregated_output") or payload.get("stdout") or payload.get("stderr") or ""
        return _limit_import_text(f"Command completed: {compact_json(command)}\nOutput:\n{output}")
    if subtype == "patch_apply_end":
        changes = payload.get("changes") or {}
        changed_files = ", ".join(list(changes.keys())[:20]) if isinstance(changes, dict) else ""
        stdout = payload.get("stdout") or ""
        return _limit_import_text(f"Patch applied. changed_files={changed_files}\n{stdout}")
    return _limit_import_text(compact_json(payload))


def _small_tool_metadata(payload: dict) -> dict:
    return {
        "turn_id": payload.get("turn_id"),
        "call_id": payload.get("call_id"),
        "tool_name": payload.get("tool_name") or payload.get("type"),
        "cwd": payload.get("cwd"),
        "success": payload.get("success"),
    }


def _limit_import_text(text: str) -> str:
    if len(text) <= MAX_CODEX_IMPORT_CONTENT_CHARS:
        return text
    return text[:MAX_CODEX_IMPORT_CONTENT_CHARS] + "\n...[truncated by AMO historical Codex importer]"
