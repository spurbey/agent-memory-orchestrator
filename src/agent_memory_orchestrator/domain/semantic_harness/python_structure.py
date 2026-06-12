from __future__ import annotations

import ast
from dataclasses import dataclass

from .identity import file_id
from .identity import normalize_file_path
from .identity import symbol_id
from .models import HarnessEdge
from .models import HarnessNode
from .models import SourceFile


@dataclass(slots=True, frozen=True)
class _SymbolRecord:
    node_id: str
    qualified_name: str
    short_name: str
    symbol_kind: str
    class_context: str
    line_start: int
    line_end: int


def add_python_structure(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    all_python_paths: tuple[str, ...],
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    try:
        tree = ast.parse(source.text)
    except SyntaxError:
        return
    lines = source.text.splitlines()
    path = normalize_file_path(source.path)
    records = _add_symbols(
        repo_id=repo_id,
        path=path,
        lines=lines,
        tree=tree,
        file_node_id=file_node_id,
        nodes=nodes,
        edges=edges,
    )
    _add_import_edges(
        repo_id=repo_id,
        source=source,
        tree=tree,
        file_node_id=file_node_id,
        all_python_paths=all_python_paths,
        edges=edges,
    )
    _add_call_edges(tree=tree, records=records, edges=edges)


def _add_symbols(
    *,
    repo_id: str,
    path: str,
    lines: list[str],
    tree: ast.AST,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> tuple[_SymbolRecord, ...]:
    records: list[_SymbolRecord] = []

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
            parent_parts = [name for name, _kind in self.stack]
            qualified = ".".join([*parent_parts, node.name]) if parent_parts else node.name
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
                parent_qualified = ".".join(parent_parts)
                parent_id = symbol_id(repo_id, path, parent_qualified or parent_name, parent_kind)
                edges.append(HarnessEdge(source_id=parent_id, target_id=node_id, kind="CONTAINS"))
            records.append(
                _SymbolRecord(
                    node_id=node_id,
                    qualified_name=qualified,
                    short_name=node.name,
                    symbol_kind=symbol_kind,
                    class_context=next((name for name, kind in reversed(self.stack) if kind == "class"), ""),
                    line_start=line_start,
                    line_end=line_end,
                )
            )

    Visitor().visit(tree)
    return tuple(records)


def _add_import_edges(
    *,
    repo_id: str,
    source: SourceFile,
    tree: ast.AST,
    file_node_id: str,
    all_python_paths: tuple[str, ...],
    edges: list[HarnessEdge],
) -> None:
    module_map = _module_map(repo_id, all_python_paths)
    source_path = normalize_file_path(source.path)
    current_module = _module_parts_for_path(source_path)
    current_package = current_module if source_path.endswith("/__init__.py") else current_module[:-1]
    seen: set[tuple[str, str, str]] = set()
    for node in ast.walk(tree):
        target_module = ""
        imported_name = ""
        if isinstance(node, ast.Import):
            for alias in node.names:
                target_module = alias.name
                imported_name = alias.asname or alias.name
                target = _resolve_module_file(target_module, module_map)
                if target:
                    _append_import_edge(edges, seen, file_node_id, target, target_module, imported_name)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import_from_module(node, current_package)
            for alias in node.names:
                alias_name = alias.name
                target = _resolve_module_file(".".join(part for part in (base, alias_name) if part), module_map)
                if not target:
                    target = _resolve_module_file(base, module_map)
                if target:
                    _append_import_edge(edges, seen, file_node_id, target, base, alias.asname or alias.name)


def _append_import_edge(
    edges: list[HarnessEdge],
    seen: set[tuple[str, str, str]],
    source_id: str,
    target_id: str,
    module: str,
    imported_name: str,
) -> None:
    key = (source_id, target_id, imported_name)
    if key in seen:
        return
    seen.add(key)
    edges.append(
        HarnessEdge(
            source_id=source_id,
            target_id=target_id,
            kind="IMPORTS",
            confidence=0.9,
            metadata={"module": module, "imported_name": imported_name, "resolution": "local_python_file"},
        )
    )


def _add_call_edges(*, tree: ast.AST, records: tuple[_SymbolRecord, ...], edges: list[HarnessEdge]) -> None:
    by_short_name: dict[str, list[_SymbolRecord]] = {}
    by_qualified_name: dict[str, _SymbolRecord] = {}
    for record in records:
        by_short_name.setdefault(record.short_name, []).append(record)
        by_qualified_name[record.qualified_name] = record
    containing = _containing_symbols(records)
    seen: dict[tuple[str, str], int] = {}
    metadata: dict[tuple[str, str], dict[str, object]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        caller = containing(int(getattr(node, "lineno", 0) or 0))
        if caller is None:
            continue
        target, resolution = _resolve_call_target(node.func, caller, by_short_name, by_qualified_name)
        if target is None or target.node_id == caller.node_id:
            continue
        key = (caller.node_id, target.node_id)
        seen[key] = seen.get(key, 0) + 1
        metadata[key] = {"resolution": resolution, "call_count": seen[key]}
    for (source_id, target_id), count in sorted(seen.items()):
        info = dict(metadata[(source_id, target_id)])
        info["call_count"] = count
        edges.append(
            HarnessEdge(
                source_id=source_id,
                target_id=target_id,
                kind="CALLS",
                confidence=0.8 if info.get("resolution") == "same_file_unique_name" else 0.75,
                metadata=info,
            )
        )


def _resolve_call_target(
    func: ast.expr,
    caller: _SymbolRecord,
    by_short_name: dict[str, list[_SymbolRecord]],
    by_qualified_name: dict[str, _SymbolRecord],
) -> tuple[_SymbolRecord | None, str]:
    if isinstance(func, ast.Name):
        matches = by_short_name.get(func.id, [])
        if len(matches) == 1:
            return matches[0], "same_file_unique_name"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self" and caller.class_context:
        target = by_qualified_name.get(f"{caller.class_context}.{func.attr}")
        if target:
            return target, "self_method_same_class"
    return None, ""


def _containing_symbols(records: tuple[_SymbolRecord, ...]):
    ordered = sorted(records, key=lambda item: (item.line_end - item.line_start, item.line_start))

    def find(line: int) -> _SymbolRecord | None:
        for record in ordered:
            if record.line_start <= line <= record.line_end and record.symbol_kind in {"function", "method"}:
                return record
        return None

    return find


def _module_map(repo_id: str, paths: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        node_id = file_id(repo_id, path)
        for key in _module_keys_for_path(path):
            out.setdefault(key, node_id)
    return out


def _module_keys_for_path(path: str) -> tuple[str, ...]:
    normalized = normalize_file_path(path)
    if not normalized.endswith(".py"):
        return ()
    without_suffix = normalized[:-3]
    if without_suffix.endswith("/__init__"):
        without_suffix = without_suffix[: -len("/__init__")]
    keys = {without_suffix.replace("/", ".")}
    if without_suffix.startswith("src/"):
        keys.add(without_suffix[4:].replace("/", "."))
    return tuple(sorted(key for key in keys if key))


def _module_parts_for_path(path: str) -> list[str]:
    key = _module_keys_for_path(path)[0] if _module_keys_for_path(path) else ""
    return [part for part in key.split(".") if part]


def _resolve_import_from_module(node: ast.ImportFrom, current_package: list[str]) -> str:
    module_parts = [part for part in str(node.module or "").split(".") if part]
    if int(node.level or 0) <= 0:
        return ".".join(module_parts)
    keep_count = max(0, len(current_package) - int(node.level or 1) + 1)
    return ".".join([*current_package[:keep_count], *module_parts])


def _resolve_module_file(module: str, module_map: dict[str, str]) -> str:
    key = ".".join(part for part in str(module or "").split(".") if part)
    return module_map.get(key, "")


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


__all__ = ["add_python_structure"]
