from __future__ import annotations

import ast
import re
from collections.abc import Iterable

from .identity import code_region_id
from .identity import file_id
from .identity import normalize_file_path
from .identity import symbol_id
from .models import HarnessEdge
from .models import HarnessNode
from .models import SourceFile
from .models import StructuralHarnessGraph


def build_structural_graph(repo_id: str, files: Iterable[SourceFile]) -> StructuralHarnessGraph:
    nodes: dict[str, HarnessNode] = {
        repo_id: HarnessNode(id=repo_id, kind="Repo", label=repo_id, repo_id=repo_id)
    }
    edges: list[HarnessEdge] = []
    for source in files:
        path = normalize_file_path(source.path)
        file_node_id = file_id(repo_id, path)
        language = source.language or _language_for_path(path)
        nodes[file_node_id] = HarnessNode(
            id=file_node_id,
            kind="File",
            label=path,
            repo_id=repo_id,
            summary=_file_summary(source.text),
            metadata={"path": path, "language": language, "line_count": _line_count(source.text)},
        )
        edges.append(HarnessEdge(source_id=repo_id, target_id=file_node_id, kind="CONTAINS"))
        if language == "python":
            _add_python_symbols(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges)
        elif language == "markdown":
            _add_markdown_regions(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges)
        elif language in {"json", "toml", "yaml"}:
            _add_config_region(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges, language=language)
    return StructuralHarnessGraph(repo_id=repo_id, nodes=tuple(nodes.values()), edges=tuple(edges))


def _add_python_symbols(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    try:
        tree = ast.parse(source.text)
    except SyntaxError:
        return
    lines = source.text.splitlines()
    path = normalize_file_path(source.path)

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[tuple[str, str]] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            self._add_symbol(node=node, symbol_kind="class")
            self.stack.append((node.name, "class"))
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._add_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._add_function(node)

        def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            symbol_kind = "method" if any(kind == "class" for _name, kind in self.stack) else "function"
            self._add_symbol(node=node, symbol_kind=symbol_kind)
            self.stack.append((node.name, symbol_kind))
            self.generic_visit(node)
            self.stack.pop()

        def _add_symbol(self, *, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, symbol_kind: str) -> None:
            parts = [name for name, _kind in self.stack]
            qualified = ".".join([*parts, node.name]) if parts else node.name
            node_id = symbol_id(repo_id, path, qualified, symbol_kind)
            line_start = int(getattr(node, "lineno", 1) or 1)
            line_end = int(getattr(node, "end_lineno", line_start) or line_start)
            nodes[node_id] = HarnessNode(
                id=node_id,
                kind="Symbol",
                label=qualified,
                repo_id=repo_id,
                summary=_symbol_summary(lines, line_start, line_end),
                metadata={
                    "path": path,
                    "qualified_name": qualified,
                    "symbol_kind": symbol_kind,
                    "line_start": line_start,
                    "line_end": line_end,
                },
            )
            edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="DEFINES"))
            if self.stack:
                parent_name, parent_kind = self.stack[-1]
                parent_qualified = ".".join([name for name, _kind in self.stack])
                parent_id = symbol_id(repo_id, path, parent_qualified or parent_name, parent_kind)
                edges.append(HarnessEdge(source_id=parent_id, target_id=node_id, kind="CONTAINS"))

    Visitor().visit(tree)


def _add_markdown_regions(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    path = normalize_file_path(source.path)
    for idx, line in enumerate(source.text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = match.group(2).strip()
        region_key = f"heading:{_slug(title)}:{idx}"
        node_id = code_region_id(repo_id, path, region_key, "markdown_section")
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="CodeRegion",
            label=title,
            repo_id=repo_id,
            summary=title,
            metadata={"path": path, "region_kind": "markdown_section", "line_start": idx, "line_end": idx},
        )
        edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS"))


def _add_config_region(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
    language: str,
) -> None:
    if not source.text.strip():
        return
    path = normalize_file_path(source.path)
    node_id = code_region_id(repo_id, path, "whole_file", "config_stanza")
    nodes[node_id] = HarnessNode(
        id=node_id,
        kind="CodeRegion",
        label=f"{path} config",
        repo_id=repo_id,
        summary=_file_summary(source.text),
        metadata={"path": path, "region_kind": "config_stanza", "language": language, "line_start": 1, "line_end": _line_count(source.text)},
    )
    edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS"))


def _language_for_path(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python",
        "md": "markdown",
        "json": "json",
        "toml": "toml",
        "yaml": "yaml",
        "yml": "yaml",
    }.get(suffix, suffix)


def _line_count(text: str) -> int:
    return len(text.splitlines()) or 1


def _file_summary(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return ""


def _symbol_summary(lines: list[str], line_start: int, line_end: int) -> str:
    if not lines:
        return ""
    start = max(1, line_start) - 1
    end = min(len(lines), max(line_start, line_end))
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "section"


__all__ = ["build_structural_graph"]
