from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


PROSE_TARGET_TOKENS = 450
LONG_BLOCK_LINES = 120


@dataclass(slots=True, frozen=True)
class ChunkCandidate:
    content_type: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def token_count(self) -> int:
        return len(self.text.split())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def classify_content_type(text: str, event_type: str = "", metadata: dict[str, Any] | None = None) -> str:
    meta = metadata or {}
    lowered = text.lower()
    stripped = text.strip()
    path = str(meta.get("path") or meta.get("file_path") or "")

    if stripped.startswith("diff --git") or re.search(r"(?m)^@@ -\d+", text):
        return "diff"
    if "traceback (most recent call last)" in lowered or re.search(r"(?m)^\s+File \".+\", line \d+", text):
        return "stacktrace"
    if any(marker in lowered for marker in ("assertionerror", "failed tests", "pytest", "npm err!", "test failed")):
        return "test_output"
    if event_type in {"tool_result", "tool_output", "post_tool_use"}:
        return "tool_output"
    if _looks_like_json(stripped):
        return "json"
    if _looks_like_code(text, path):
        return "code"
    if event_type in {"prompt", "response", "message", "user_prompt_submit"}:
        return "chat_message"
    return "prose"


def chunk_text(text: str, event_type: str = "", metadata: dict[str, Any] | None = None) -> list[ChunkCandidate]:
    content_type = classify_content_type(text, event_type, metadata)
    clean = text.strip()
    if not clean:
        return []

    if content_type == "diff":
        return _chunk_diff(clean)
    if content_type == "code":
        return _chunk_code(clean, metadata or {})
    if content_type in {"stacktrace", "test_output", "tool_output", "json"}:
        return _chunk_by_lines(clean, content_type)
    return _chunk_prose(clean, content_type)


def _looks_like_json(text: str) -> bool:
    if not text or text[0] not in "[{":
        return False
    try:
        json.loads(text)
    except Exception:
        return False
    return True


def _looks_like_code(text: str, path: str = "") -> bool:
    if re.search(r"\.(py|js|ts|tsx|jsx|go|rs|java|kt|swift|dart|cpp|c|h|cs|rb|php)$", path):
        return True
    code_markers = (
        r"(?m)^\s*(async\s+def|def|class)\s+\w+",
        r"(?m)^\s*(export\s+)?(async\s+)?function\s+\w+",
        r"(?m)^\s*(const|let|var)\s+\w+\s*=",
        r"(?m)^\s*(pub\s+)?fn\s+\w+",
        r"(?m)^\s*import\s+[\w.{*]",
    )
    return any(re.search(marker, text) for marker in code_markers)


def _chunk_diff(text: str) -> list[ChunkCandidate]:
    chunks: list[ChunkCandidate] = []
    file_blocks = re.split(r"(?m)(?=^diff --git )", text)
    for file_block in [block for block in file_blocks if block.strip()]:
        file_match = re.search(r"^diff --git a/(.*?) b/(.*?)$", file_block, re.MULTILINE)
        path = file_match.group(2) if file_match else ""
        headers, hunks = _split_diff_hunks(file_block)
        if not hunks:
            chunks.append(ChunkCandidate("diff", file_block.strip(), {"path": path, "hunk_index": 0}))
            continue
        for idx, hunk in enumerate(hunks):
            hunk_text = f"{headers}\n{hunk}".strip()
            chunks.append(ChunkCandidate("diff", hunk_text, {"path": path, "hunk_index": idx}))
    return chunks


def _split_diff_hunks(file_block: str) -> tuple[str, list[str]]:
    parts = re.split(r"(?m)(?=^@@ )", file_block)
    if len(parts) <= 1:
        return file_block.strip(), []
    return parts[0].strip(), [part.strip() for part in parts[1:] if part.strip()]


def _chunk_code(text: str, metadata: dict[str, Any]) -> list[ChunkCandidate]:
    lines = text.splitlines()
    symbol_starts: list[int] = []
    symbol_re = re.compile(
        r"^\s*(async\s+def|def|class|export\s+function|function|const|let|var|pub\s+fn|fn)\s+([A-Za-z_][\w]*)"
    )
    for idx, line in enumerate(lines):
        if symbol_re.search(line):
            symbol_starts.append(idx)

    if not symbol_starts or len(lines) <= LONG_BLOCK_LINES:
        return [ChunkCandidate("code", text, {"path": metadata.get("path") or metadata.get("file_path", "")})]

    chunks: list[ChunkCandidate] = []
    for offset, start in enumerate(symbol_starts):
        end = symbol_starts[offset + 1] if offset + 1 < len(symbol_starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        if block:
            symbol_match = symbol_re.search(lines[start])
            chunks.append(
                ChunkCandidate(
                    "code",
                    block,
                    {
                        "path": metadata.get("path") or metadata.get("file_path", ""),
                        "symbol": symbol_match.group(2) if symbol_match else "",
                    },
                )
            )
    return chunks


def _chunk_by_lines(text: str, content_type: str) -> list[ChunkCandidate]:
    lines = text.splitlines()
    if len(lines) <= LONG_BLOCK_LINES:
        return [ChunkCandidate(content_type, text)]
    chunks: list[ChunkCandidate] = []
    header = "\n".join(lines[:5])
    for idx in range(0, len(lines), LONG_BLOCK_LINES):
        block = "\n".join(lines[idx : idx + LONG_BLOCK_LINES]).strip()
        if idx > 0 and content_type in {"stacktrace", "test_output"}:
            block = f"{header}\n...\n{block}"
        chunks.append(ChunkCandidate(content_type, block, {"line_start": idx + 1}))
    return chunks


def _chunk_prose(text: str, content_type: str) -> list[ChunkCandidate]:
    words = text.split()
    if len(words) <= PROSE_TARGET_TOKENS:
        return [ChunkCandidate(content_type, text)]
    chunks: list[ChunkCandidate] = []
    for idx in range(0, len(words), PROSE_TARGET_TOKENS):
        part = " ".join(words[idx : idx + PROSE_TARGET_TOKENS])
        chunks.append(ChunkCandidate(content_type, part, {"token_start": idx}))
    return chunks
