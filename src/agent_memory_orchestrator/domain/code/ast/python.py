from __future__ import annotations

import ast

from ..models import CodeHunk
from .models import AstExpansion
from .utils import PYTHON_LARGE_ADD_HUNK_LINE_THRESHOLD
from .utils import PYTHON_MAX_EXPANSIONS_PER_HUNK
from .utils import PYTHON_MAX_MODULE_ASSIGNMENT_BLOCKS
from .utils import _hunk_line_range
from .utils import _patch_changed_lines
from .utils import _snippet
from .utils import _structural_id
from .utils import should_accept_ast_parent


def python_ast_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    source = "\n".join(lines)
    if not source.strip():
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        start, end = _hunk_line_range(hunk, len(lines))
        return (
            AstExpansion(
                ast_type="unparsed_hunk",
                line_start=start,
                line_end=end,
                content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
                ast_status="unparsed_syntax_error",
                language="python",
                diagnostics=(f"syntax_error:{exc.lineno or 0}",),
            ),
        )
    start, end = _hunk_line_range(hunk, len(lines))
    if hunk.old_count == 0 and hunk.new_count >= PYTHON_LARGE_ADD_HUNK_LINE_THRESHOLD:
        compact = _compact_python_hunk_regions(
            tree,
            lines,
            hunk.file_path,
            start,
            end,
            reason="large_added_file_hunk",
        )
        if compact:
            return compact
    changed_lines = range(start, end + 1)
    selected: dict[tuple[str, int, int, str], AstExpansion] = {}
    meaningful = [_python_node_region(node, lines, hunk.file_path) for node in ast.walk(tree)]
    meaningful = [region for region in meaningful if region is not None]
    for line_no in changed_lines:
        containing = [
            region
            for region in meaningful
            if region.line_start <= line_no <= region.line_end
            and should_accept_ast_parent(max(1, hunk.new_count), max(1, region.line_end - region.line_start + 1))
        ]
        if not containing:
            continue
        best = min(containing, key=lambda region: (region.line_end - region.line_start, _node_priority(region.ast_type)))
        selected[(best.ast_type, best.line_start, best.line_end, best.symbol_name)] = best
    if selected:
        expansions = tuple(selected.values())
        if len(expansions) > PYTHON_MAX_EXPANSIONS_PER_HUNK:
            compact = _compact_python_hunk_regions(
                tree,
                lines,
                hunk.file_path,
                start,
                end,
                reason="too_many_ast_regions",
            )
            if compact:
                return compact
        return expansions
    start, end = _hunk_line_range(hunk, len(lines))
    return (
        AstExpansion(
            ast_type="unparsed_hunk",
            line_start=start,
            line_end=end,
            content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
            ast_status="unparsed_no_small_ast_parent",
            language="python",
            diagnostics=("no_ast_parent_within_3x_hunk",),
        ),
    )

def _python_node_region(node: ast.AST, lines: list[str], file_path: str) -> AstExpansion | None:
    if not _is_meaningful_python_node(node):
        return None
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    symbol_name, symbol_kind = _python_symbol(node)
    ast_type = type(node).__name__
    return AstExpansion(
        ast_type=ast_type,
        line_start=start,
        line_end=end,
        content=_snippet(lines, start, end),
        language="python",
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        structural_id=_structural_id(file_path, ast_type, symbol_name, start, end),
        metadata={"python_ast_type": ast_type},
    )


def _compact_python_hunk_regions(
    tree: ast.Module,
    lines: list[str],
    file_path: str,
    start: int,
    end: int,
    *,
    reason: str,
) -> tuple[AstExpansion, ...]:
    regions: list[AstExpansion] = []
    import_ranges: list[tuple[int, int]] = []
    assignment_ranges: list[tuple[int, int]] = []

    for node in tree.body:
        node_start = getattr(node, "lineno", None)
        node_end = getattr(node, "end_lineno", None)
        if not isinstance(node_start, int) or not isinstance(node_end, int):
            continue
        if node_end < start or node_start > end:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            region = _python_node_region(node, lines, file_path)
            if region is not None:
                regions.append(_with_compaction_metadata(region, reason))
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_ranges.append((node_start, node_end))
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            assignment_ranges.append((node_start, node_end))

    regions.extend(
        _block_regions(
            import_ranges,
            lines,
            file_path,
            ast_type="ImportBlock",
            symbol_kind="import_block",
            reason=reason,
        )
    )
    assignment_blocks = _block_regions(
        assignment_ranges,
        lines,
        file_path,
        ast_type="ModuleAssignmentBlock",
        symbol_kind="module_assignment_block",
        reason=reason,
    )
    regions.extend(assignment_blocks[:PYTHON_MAX_MODULE_ASSIGNMENT_BLOCKS])

    if not regions:
        return ()
    regions.sort(key=lambda region: (region.line_start, region.line_end, region.ast_type))
    if len(regions) > PYTHON_MAX_EXPANSIONS_PER_HUNK:
        regions = regions[:PYTHON_MAX_EXPANSIONS_PER_HUNK]
    return tuple(regions)


def _with_compaction_metadata(region: AstExpansion, reason: str) -> AstExpansion:
    metadata = dict(region.metadata or {})
    metadata["compaction_reason"] = reason
    return AstExpansion(
        ast_type=region.ast_type,
        line_start=region.line_start,
        line_end=region.line_end,
        content=region.content,
        ast_status=region.ast_status,
        language=region.language,
        symbol_name=region.symbol_name,
        symbol_kind=region.symbol_kind,
        structural_id=region.structural_id,
        diagnostics=(*region.diagnostics, f"compacted:{reason}"),
        metadata=metadata,
    )


def _block_regions(
    ranges: list[tuple[int, int]],
    lines: list[str],
    file_path: str,
    *,
    ast_type: str,
    symbol_kind: str,
    reason: str,
) -> list[AstExpansion]:
    regions: list[AstExpansion] = []
    for block_start, block_end in _merge_contiguous_ranges(sorted(ranges)):
        symbol_name = f"{symbol_kind}:{block_start}-{block_end}"
        regions.append(
            AstExpansion(
                ast_type=ast_type,
                line_start=block_start,
                line_end=block_end,
                content=_snippet(lines, block_start, block_end),
                language="python",
                symbol_name=symbol_name,
                symbol_kind=symbol_kind,
                structural_id=_structural_id(file_path, ast_type, symbol_name, block_start, block_end),
                diagnostics=(f"compacted:{reason}",),
                metadata={"python_ast_type": ast_type, "compaction_reason": reason},
            )
        )
    return regions


def _merge_contiguous_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged: list[tuple[int, int]] = []
    current_start, current_end = ranges[0]
    for start, end in ranges[1:]:
        if start <= current_end + 1:
            current_end = max(current_end, end)
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def _is_meaningful_python_node(node: ast.AST) -> bool:
    return isinstance(
        node,
        (
            ast.AsyncFunctionDef,
            ast.FunctionDef,
            ast.ClassDef,
            ast.Assign,
            ast.AnnAssign,
            ast.AugAssign,
            ast.Import,
            ast.ImportFrom,
            ast.Raise,
            ast.Assert,
            ast.Return,
            ast.Expr,
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.With,
            ast.AsyncWith,
            ast.Try,
        ),
    )


def _python_symbol(node: ast.AST) -> tuple[str, str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, "function"
    if isinstance(node, ast.ClassDef):
        return node.name, "class"
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[attr-defined]
        names = [_target_name(target) for target in targets]
        return ",".join(name for name in names if name), "assignment"
    if isinstance(node, ast.Import):
        return ",".join(alias.name for alias in node.names), "import"
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return ",".join(f"{module}.{alias.name}".strip(".") for alias in node.names), "import"
    return "", type(node).__name__.lower()


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _target_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Subscript):
        return _target_name(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return ",".join(_target_name(item) for item in node.elts)
    return ""


def _node_priority(ast_type: str) -> int:
    priority = {
        "Assign": 0,
        "AnnAssign": 0,
        "AugAssign": 0,
        "Raise": 0,
        "Assert": 0,
        "Return": 0,
        "Expr": 1,
        "FunctionDef": 2,
        "AsyncFunctionDef": 2,
        "ClassDef": 3,
    }
    return priority.get(ast_type, 4)


__all__ = ["python_ast_expander"]
