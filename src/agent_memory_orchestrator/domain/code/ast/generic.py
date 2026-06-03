from __future__ import annotations

import re

from ..models import CodeHunk
from .models import AstExpansion
from .utils import BRACE_CONTROL_KEYWORDS
from .utils import GENERIC_MAX_EXPANSIONS_PER_HUNK
from .utils import _hunk_line_range
from .utils import _language_for_file
from .utils import _patch_changed_lines
from .utils import _snippet
from .utils import _structural_id
from .utils import should_accept_generic_parent


def brace_language_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    start, end = _hunk_line_range(hunk, len(lines))
    regions: dict[tuple[int, int, str], AstExpansion] = {}
    language = _language_for_file(hunk.file_path)
    for line_no in range(start, end + 1):
        region = _brace_region_for_line(hunk.file_path, lines, line_no, language=language)
        if region is None:
            continue
        if should_accept_generic_parent(max(1, hunk.new_count), max(1, region.line_end - region.line_start + 1)):
            regions[(region.line_start, region.line_end, region.symbol_name)] = region
    if regions:
        return tuple(sorted(regions.values(), key=lambda item: (item.line_start, item.line_end))[:GENERIC_MAX_EXPANSIONS_PER_HUNK])
    return (
        AstExpansion(
            ast_type="UnparsedHunk",
            line_start=start,
            line_end=end,
            content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
            ast_status="unparsed_no_brace_region",
            language=language,
            symbol_kind="unparsed_hunk",
            structural_id=_structural_id(hunk.file_path, "UnparsedHunk", "", start, end),
        ),
    )


def css_rule_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    start, end = _hunk_line_range(hunk, len(lines))
    regions: dict[tuple[int, int, str], AstExpansion] = {}
    for line_no in range(start, end + 1):
        region = _css_region_for_line(hunk.file_path, lines, line_no)
        if region is not None:
            regions[(region.line_start, region.line_end, region.symbol_name)] = region
    if regions:
        return tuple(sorted(regions.values(), key=lambda item: (item.line_start, item.line_end))[:GENERIC_MAX_EXPANSIONS_PER_HUNK])
    return _generic_text_region(hunk, lines, language="css", ast_status="unparsed_no_css_rule")


def markup_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    start, end = _hunk_line_range(hunk, len(lines))
    regions: dict[tuple[int, int, str], AstExpansion] = {}
    for line_no in range(start, end + 1):
        region = _markup_region_for_line(hunk.file_path, lines, line_no)
        if region is not None:
            regions[(region.line_start, region.line_end, region.symbol_name)] = region
    if regions:
        return tuple(sorted(regions.values(), key=lambda item: (item.line_start, item.line_end))[:GENERIC_MAX_EXPANSIONS_PER_HUNK])
    return _generic_text_region(hunk, lines, language="markup", ast_status="unparsed_no_markup_region")


def structured_config_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    start, end = _hunk_line_range(hunk, len(lines))
    language = _language_for_file(hunk.file_path)
    regions: list[AstExpansion] = []
    for line_no in range(start, end + 1):
        line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
        key = _config_key(line)
        if not key:
            continue
        regions.append(
            AstExpansion(
                ast_type="ConfigEntry",
                line_start=line_no,
                line_end=line_no,
                content=line.strip(),
                language=language,
                symbol_name=key,
                symbol_kind="config_key",
                structural_id=_structural_id(hunk.file_path, "ConfigEntry", key, line_no, line_no),
                metadata={"config_key": key},
            )
        )
    if regions:
        return tuple(regions[:GENERIC_MAX_EXPANSIONS_PER_HUNK])
    return _generic_text_region(hunk, lines, language=language, ast_status="unparsed_no_config_key")


def markdown_section_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    start, end = _hunk_line_range(hunk, len(lines))
    header_start = start
    header = ""
    for index in range(start, 0, -1):
        line = lines[index - 1] if index <= len(lines) else ""
        if line.lstrip().startswith("#"):
            header_start = index
            header = line.strip("# ").strip()
            break
    section_end = end
    for index in range(max(end + 1, header_start + 1), len(lines) + 1):
        line = lines[index - 1]
        if line.lstrip().startswith("#"):
            break
        section_end = index
    return (
        AstExpansion(
            ast_type="MarkdownSection",
            line_start=header_start,
            line_end=section_end,
            content=_snippet(lines, header_start, section_end) or _patch_changed_lines(hunk.patch),
            language="markdown",
            symbol_name=header or f"section:{header_start}-{section_end}",
            symbol_kind="doc_section",
            structural_id=_structural_id(hunk.file_path, "MarkdownSection", header or "", header_start, section_end),
        ),
    )


def _brace_region_for_line(file_path: str, lines: list[str], line_no: int, *, language: str) -> AstExpansion | None:
    if not lines or not (1 <= line_no <= len(lines)):
        return None
    start = line_no
    while start > 1:
        text = lines[start - 1]
        if "{" in text and _brace_symbol(text)[0]:
            break
        start -= 1
    if "{" not in lines[start - 1]:
        return _single_line_brace_expansion(file_path, lines, line_no, language=language)
    balance = 0
    end = start
    for index in range(start, len(lines) + 1):
        balance += lines[index - 1].count("{")
        balance -= lines[index - 1].count("}")
        end = index
        if balance <= 0 and index > start:
            break
    symbol_name, symbol_kind, ast_type = _brace_symbol(lines[start - 1])
    if not symbol_name:
        return _single_line_brace_expansion(file_path, lines, line_no, language=language)
    return AstExpansion(
        ast_type=ast_type,
        line_start=start,
        line_end=end,
        content=_snippet(lines, start, end),
        language=language,
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        structural_id=_structural_id(file_path, ast_type, symbol_name, start, end),
    )


def _single_line_brace_expansion(file_path: str, lines: list[str], line_no: int, *, language: str) -> AstExpansion | None:
    line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
    symbol_name, symbol_kind, ast_type = _brace_symbol(line)
    if not symbol_name:
        return None
    return AstExpansion(
        ast_type=ast_type,
        line_start=line_no,
        line_end=line_no,
        content=line.strip(),
        language=language,
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        structural_id=_structural_id(file_path, ast_type, symbol_name, line_no, line_no),
    )


def _brace_symbol(line: str) -> tuple[str, str, str]:
    text = line.strip()
    patterns = (
        (r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "function", "FunctionDeclaration"),
        (r"(?:export\s+default\s+)?class\s+([A-Za-z_$][\w$]*)", "class", "ClassDeclaration"),
        (r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", "function", "ArrowFunction"),
        (r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?function", "function", "FunctionExpression"),
        (r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", "assignment", "Assignment"),
        (r"([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?\([^)]*\)\s*=>", "function", "ObjectMethod"),
        (r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", "function", "MethodDeclaration"),
        (r"(?:class|abstract class)\s+([A-Za-z_$][\w$]*)", "class", "ClassDeclaration"),
    )
    for pattern, symbol_kind, ast_type in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1)
            if name in BRACE_CONTROL_KEYWORDS:
                continue
            return name, symbol_kind, ast_type
    return "", "", ""


def _css_region_for_line(file_path: str, lines: list[str], line_no: int) -> AstExpansion | None:
    start = line_no
    while start > 1 and "{" not in lines[start - 1]:
        start -= 1
    if "{" not in lines[start - 1]:
        return None
    selector = lines[start - 1].split("{", 1)[0].strip()
    if not selector:
        return None
    balance = 0
    end = start
    for index in range(start, len(lines) + 1):
        balance += lines[index - 1].count("{")
        balance -= lines[index - 1].count("}")
        end = index
        if balance <= 0 and index > start:
            break
    return AstExpansion(
        ast_type="CssRule",
        line_start=start,
        line_end=end,
        content=_snippet(lines, start, end),
        language="css",
        symbol_name=selector[:160],
        symbol_kind="style_rule",
        structural_id=_structural_id(file_path, "CssRule", selector[:160], start, end),
    )


def _markup_region_for_line(file_path: str, lines: list[str], line_no: int) -> AstExpansion | None:
    line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
    match = re.search(r"<([A-Za-z][\w:-]*)([^>]*)>", line)
    if not match:
        return None
    tag = match.group(1)
    attrs = match.group(2)
    identity = _markup_identity(tag, attrs)
    return AstExpansion(
        ast_type="MarkupElement",
        line_start=line_no,
        line_end=line_no,
        content=line.strip(),
        language="markup",
        symbol_name=identity,
        symbol_kind="markup_element",
        structural_id=_structural_id(file_path, "MarkupElement", identity, line_no, line_no),
    )


def _markup_identity(tag: str, attrs: str) -> str:
    id_match = re.search(r'id=["\']([^"\']+)["\']', attrs)
    class_match = re.search(r'class=["\']([^"\']+)["\']', attrs)
    if id_match:
        return f"{tag}#{id_match.group(1)}"
    if class_match:
        return f"{tag}.{class_match.group(1).split()[0]}"
    return tag


def _config_key(line: str) -> str:
    stripped = line.strip().strip(",")
    if not stripped or stripped.startswith(("#", "//")):
        return ""
    match = re.match(r'["\']?([A-Za-z0-9_.-]+)["\']?\s*[:=]', stripped)
    return match.group(1) if match else ""


def _generic_text_region(hunk: CodeHunk, lines: list[str], *, language: str, ast_status: str) -> tuple[AstExpansion, ...]:
    start, end = _hunk_line_range(hunk, len(lines))
    return (
        AstExpansion(
            ast_type="TextHunk",
            line_start=start,
            line_end=end,
            content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
            ast_status=ast_status,
            language=language,
            symbol_kind="text_hunk",
            structural_id=_structural_id(hunk.file_path, "TextHunk", "", start, end),
        ),
    )


__all__ = [
    "brace_language_expander",
    "css_rule_expander",
    "markdown_section_expander",
    "markup_expander",
    "structured_config_expander",
]
