from __future__ import annotations

import re

from ..identity import doc_section_id
from ..identity import normalize_file_path
from ..models import HarnessNode
from ..models import SourceFile
from .models import DocSemanticArtifact


def extract_markdown_doc_sections(repo_id: str, source: SourceFile, file_node_id: str) -> tuple[DocSemanticArtifact, ...]:
    path = normalize_file_path(source.path)
    if (source.language or "").lower() != "markdown" and not path.lower().endswith(".md"):
        return ()
    lines = source.text.splitlines()
    headings = _heading_lines(lines)
    if not headings and source.text.strip():
        headings = ((1, 1, path),)
    artifacts: list[DocSemanticArtifact] = []
    for index, (line_start, level, title) in enumerate(headings):
        line_end = _section_end(headings, index, len(lines), level)
        section_text = "\n".join(lines[line_start - 1 : line_end]).strip()
        node_id = doc_section_id(repo_id, path, title, line_start)
        artifacts.append(
            DocSemanticArtifact(
                node=HarnessNode(
                    id=node_id,
                    kind="DocSection",
                    label=title,
                    repo_id=repo_id,
                    summary=_summary(section_text, fallback=title),
                    metadata={
                        "path": path,
                        "doc_kind": "markdown_section",
                        "heading": title,
                        "heading_level": level,
                        "line_start": line_start,
                        "line_end": line_end,
                        "content_excerpt": section_text[:600],
                    },
                ),
                text=section_text,
                source_file_id=file_node_id,
            )
        )
    return tuple(artifacts)


def _heading_lines(lines: list[str]) -> tuple[tuple[int, int, str], ...]:
    out: list[tuple[int, int, str]] = []
    for line_no, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        out.append((line_no, len(match.group(1)), match.group(2).strip()))
    return tuple(out)


def _section_end(headings: tuple[tuple[int, int, str], ...], index: int, total_lines: int, level: int) -> int:
    for next_line_start, next_level, _title in headings[index + 1 :]:
        if next_level <= level:
            return max(1, next_line_start - 1)
    return max(1, total_lines)


def _summary(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:160]
    return fallback[:160]


__all__ = ["extract_markdown_doc_sections"]
