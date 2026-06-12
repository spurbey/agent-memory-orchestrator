from __future__ import annotations

import ast
from dataclasses import dataclass

from .identity import file_id
from .identity import normalize_file_path
from .models import HarnessEdge
from .models import HarnessNode
from .models import SourceFile


@dataclass(slots=True, frozen=True)
class _SymbolRef:
    node_id: str
    label: str
    path: str
    qualified_name: str
    short_name: str
    symbol_kind: str
    line_start: int
    line_end: int


@dataclass(slots=True, frozen=True)
class _ModuleAlias:
    path: str
    module_name: str
    symbols: tuple[_SymbolRef, ...]


@dataclass(slots=True, frozen=True)
class _ImportBindings:
    symbol_aliases: dict[str, _SymbolRef]
    module_aliases: dict[str, _ModuleAlias]


def add_python_cross_file_call_edges(
    *,
    repo_id: str,
    sources: tuple[SourceFile, ...],
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    """Add conservative CALLS edges from imported local names to target symbols.

    This intentionally handles only parser-grounded local imports. It does not
    infer dynamic attributes, wildcard imports, or unresolved third-party calls.
    """

    python_sources = tuple(source for source in sources if _is_python(source))
    if not python_sources:
        return
    source_paths = tuple(normalize_file_path(source.path) for source in python_sources)
    module_map = _module_map(repo_id, source_paths)
    symbols_by_file = _symbols_by_file(nodes)
    edge_counts: dict[tuple[str, str, str], int] = {}
    edge_metadata: dict[tuple[str, str, str], dict[str, object]] = {}
    for source in python_sources:
        path = normalize_file_path(source.path)
        try:
            tree = ast.parse(source.text)
        except SyntaxError:
            continue
        callers = _caller_symbols(symbols_by_file.get(path, ()))
        if not callers:
            continue
        bindings = _import_bindings(
            repo_id=repo_id,
            source_path=path,
            tree=tree,
            module_map=module_map,
            symbols_by_file=symbols_by_file,
        )
        if not bindings.symbol_aliases and not bindings.module_aliases:
            continue
        containing = _containing_symbols(callers)
        for call_node in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            caller = containing(int(getattr(call_node, "lineno", 0) or 0))
            if caller is None:
                continue
            target, metadata = _resolve_imported_call(call_node.func, bindings)
            if target is None or target.node_id == caller.node_id:
                continue
            key = (caller.node_id, target.node_id, "CALLS")
            edge_counts[key] = edge_counts.get(key, 0) + 1
            edge_metadata[key] = metadata
    for (source_id, target_id, kind), count in sorted(edge_counts.items()):
        metadata = dict(edge_metadata[(source_id, target_id, kind)])
        metadata["call_count"] = count
        edges.append(
            HarnessEdge(
                source_id=source_id,
                target_id=target_id,
                kind=kind,
                confidence=0.78 if metadata.get("resolution") == "imported_symbol_name" else 0.72,
                metadata=metadata,
            )
        )


def _import_bindings(
    *,
    repo_id: str,
    source_path: str,
    tree: ast.AST,
    module_map: dict[str, str],
    symbols_by_file: dict[str, tuple[_SymbolRef, ...]],
) -> _ImportBindings:
    current_module = _module_parts_for_path(source_path)
    current_package = current_module if source_path.endswith("/__init__.py") else current_module[:-1]
    symbol_aliases: dict[str, _SymbolRef] = {}
    module_aliases: dict[str, _ModuleAlias] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.asname and "." in alias.name:
                    continue
                target_file_id = _resolve_module_file(alias.name, module_map)
                if not target_file_id:
                    continue
                local_name = alias.asname or alias.name
                target_path = _file_path_from_id(repo_id, target_file_id)
                module_aliases[local_name] = _ModuleAlias(
                    path=target_path,
                    module_name=alias.name,
                    symbols=symbols_by_file.get(target_path, ()),
                )
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import_from_module(node, current_package)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                submodule_file_id = _resolve_module_file(".".join(part for part in (base, alias.name) if part), module_map)
                if submodule_file_id:
                    target_path = _file_path_from_id(repo_id, submodule_file_id)
                    module_aliases[local_name] = _ModuleAlias(
                        path=target_path,
                        module_name=".".join(part for part in (base, alias.name) if part),
                        symbols=symbols_by_file.get(target_path, ()),
                    )
                    continue
                target_file_id = _resolve_module_file(base, module_map)
                if not target_file_id:
                    continue
                target_path = _file_path_from_id(repo_id, target_file_id)
                target_symbol = _unique_top_level_symbol(symbols_by_file.get(target_path, ()), alias.name)
                if target_symbol is not None:
                    symbol_aliases[local_name] = target_symbol
    return _ImportBindings(symbol_aliases=symbol_aliases, module_aliases=module_aliases)


def _resolve_imported_call(
    func: ast.expr,
    bindings: _ImportBindings,
) -> tuple[_SymbolRef | None, dict[str, object]]:
    if isinstance(func, ast.Name):
        target = bindings.symbol_aliases.get(func.id)
        if target is not None:
            return target, {
                "resolution": "imported_symbol_name",
                "imported_name": func.id,
                "target_path": target.path,
            }
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module_binding = bindings.module_aliases.get(func.value.id)
        if module_binding is None:
            return None, {}
        target = _unique_top_level_symbol(module_binding.symbols, func.attr)
        if target is None:
            return None, {}
        return target, {
            "resolution": "imported_module_attribute",
            "imported_name": f"{func.value.id}.{func.attr}",
            "module": module_binding.module_name,
            "target_path": module_binding.path,
        }
    return None, {}


def _symbols_by_file(nodes: dict[str, HarnessNode]) -> dict[str, tuple[_SymbolRef, ...]]:
    out: dict[str, list[_SymbolRef]] = {}
    for node in nodes.values():
        if node.kind != "Symbol":
            continue
        path = normalize_file_path(str(node.metadata.get("path") or ""))
        if not path:
            continue
        qualified_name = str(node.metadata.get("qualified_name") or node.label)
        ref = _SymbolRef(
            node_id=node.id,
            label=node.label,
            path=path,
            qualified_name=qualified_name,
            short_name=qualified_name.rsplit(".", 1)[-1],
            symbol_kind=str(node.metadata.get("symbol_kind") or ""),
            line_start=int(node.metadata.get("line_start") or 0),
            line_end=int(node.metadata.get("line_end") or 0),
        )
        out.setdefault(path, []).append(ref)
    return {path: tuple(sorted(symbols, key=lambda item: (item.line_start, item.qualified_name))) for path, symbols in out.items()}


def _caller_symbols(symbols: tuple[_SymbolRef, ...]) -> tuple[_SymbolRef, ...]:
    return tuple(symbol for symbol in symbols if symbol.symbol_kind in {"function", "method"} and symbol.line_start > 0)


def _unique_top_level_symbol(symbols: tuple[_SymbolRef, ...], short_name: str) -> _SymbolRef | None:
    matches = tuple(
        symbol
        for symbol in symbols
        if symbol.short_name == short_name and "." not in symbol.qualified_name and symbol.symbol_kind in {"function", "class"}
    )
    return matches[0] if len(matches) == 1 else None


def _containing_symbols(records: tuple[_SymbolRef, ...]):
    ordered = sorted(records, key=lambda item: (item.line_end - item.line_start, item.line_start))

    def find(line: int) -> _SymbolRef | None:
        for record in ordered:
            if record.line_start <= line <= record.line_end:
                return record
        return None

    return find


def _is_python(source: SourceFile) -> bool:
    path = normalize_file_path(source.path)
    return (source.language or "").lower() == "python" or path.endswith(".py")


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


def _file_path_from_id(repo_id: str, node_id: str) -> str:
    prefix = f"file:{repo_id}:"
    return normalize_file_path(node_id.removeprefix(prefix))


__all__ = ["add_python_cross_file_call_edges"]
