from __future__ import annotations

import re

from .identity import code_region_id
from .identity import normalize_file_path
from .identity import symbol_id
from .models import HarnessEdge
from .models import HarnessNode
from .models import SourceFile


JS_TS_LANGUAGES = {"js", "jsx", "ts", "tsx"}

_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+default\s+|export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
_JS_FUNCTION_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
_JS_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
_JS_FUNCTION_VALUE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?function\b"
)
_CSS_SELECTOR_RE = re.compile(r"^\s*([^@{};][^{;]+?)\s*\{")
_HTML_TAG_RE = re.compile(r"<([A-Za-z][\w:-]*)([^>]*)>")
_HTML_ID_RE = re.compile(r"""\bid=["']([^"']+)["']""")
_HTML_CLASS_RE = re.compile(r"""\bclass=["']([^"']+)["']""")


def add_web_structure(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    language: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    """Add conservative product-level structure for common web files.

    This intentionally does not create raw AST nodes. It only promotes named
    JS/TS declarations and bounded CSS/HTML regions that are useful anchors for
    harness queries.
    """

    if language in JS_TS_LANGUAGES:
        _add_js_ts_symbols(repo_id=repo_id, source=source, file_node_id=file_node_id, language=language, nodes=nodes, edges=edges)
    elif language == "css":
        _add_css_regions(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges)
    elif language == "html":
        _add_html_regions(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges)


def _add_js_ts_symbols(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    language: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    path = normalize_file_path(source.path)
    seen: set[tuple[str, str]] = set()
    for line_no, line in enumerate(source.text.splitlines(), start=1):
        name = ""
        symbol_kind = ""
        if match := _JS_CLASS_RE.match(line):
            name = match.group(1)
            symbol_kind = "class"
        elif match := _JS_FUNCTION_RE.match(line):
            name = match.group(1)
            symbol_kind = _function_symbol_kind(name)
        elif match := _JS_ARROW_RE.match(line):
            name = match.group(1)
            symbol_kind = _function_symbol_kind(name)
        elif match := _JS_FUNCTION_VALUE_RE.match(line):
            name = match.group(1)
            symbol_kind = _function_symbol_kind(name)
        if not name or (name, symbol_kind) in seen:
            continue
        seen.add((name, symbol_kind))
        node_id = symbol_id(repo_id, path, name, symbol_kind)
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="Symbol",
            label=name,
            repo_id=repo_id,
            summary=f"{symbol_kind} {name}",
            metadata={
                "path": path,
                "language": language,
                "symbol_kind": symbol_kind,
                "line_start": line_no,
                "line_end": line_no,
                "extractor": "regex_web_v1",
            },
        )
        edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="DEFINES", confidence=0.72))


def _add_css_regions(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    path = normalize_file_path(source.path)
    for line_no, line in enumerate(source.text.splitlines(), start=1):
        match = _CSS_SELECTOR_RE.match(line)
        if not match:
            continue
        selector = " ".join(match.group(1).strip().split())
        if not selector:
            continue
        region_key = f"selector:{_slug(selector)}:{line_no}"
        node_id = code_region_id(repo_id, path, region_key, "css_selector")
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="CodeRegion",
            label=selector,
            repo_id=repo_id,
            summary=f"CSS selector {selector}",
            metadata={
                "path": path,
                "region_kind": "css_selector",
                "line_start": line_no,
                "line_end": line_no,
                "extractor": "regex_web_v1",
            },
        )
        edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS", confidence=0.7))


def _add_html_regions(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    path = normalize_file_path(source.path)
    for line_no, line in enumerate(source.text.splitlines(), start=1):
        match = _HTML_TAG_RE.search(line)
        if not match:
            continue
        tag = match.group(1).lower()
        attrs = match.group(2)
        html_id = _first_match(_HTML_ID_RE, attrs)
        classes = tuple(_first_match(_HTML_CLASS_RE, attrs).split())
        if not html_id and not classes:
            continue
        label = _html_label(tag=tag, html_id=html_id, classes=classes)
        region_key = f"element:{_slug(label)}:{line_no}"
        node_id = code_region_id(repo_id, path, region_key, "html_element")
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="CodeRegion",
            label=label,
            repo_id=repo_id,
            summary=f"HTML element {label}",
            metadata={
                "path": path,
                "region_kind": "html_element",
                "line_start": line_no,
                "line_end": line_no,
                "tag": tag,
                "id": html_id,
                "classes": list(classes),
                "extractor": "regex_web_v1",
            },
        )
        edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS", confidence=0.66))


def _function_symbol_kind(name: str) -> str:
    return "component" if name[:1].isupper() else "function"


def _first_match(pattern: re.Pattern[str], value: str) -> str:
    match = pattern.search(value)
    return match.group(1).strip() if match else ""


def _html_label(*, tag: str, html_id: str, classes: tuple[str, ...]) -> str:
    label = tag
    if html_id:
        label += f"#{html_id}"
    for class_name in classes[:3]:
        label += f".{class_name}"
    return label


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "region"


__all__ = ["JS_TS_LANGUAGES", "add_web_structure"]
