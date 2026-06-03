"""Code analysis and code identity domain contracts."""

from __future__ import annotations

__all__ = [
    "AstExpander",
    "AstExpansion",
    "AstExpansionResult",
    "CodeHunk",
    "CodeNode",
    "CodeVersionPlan",
    "CodeVersionRecord",
    "CodeVersionRelation",
    "SymbolRecord",
    "code_nodes_from_hunks",
    "default_ast_expander",
    "extract_code_nodes_from_commit",
    "git_file_at_commit",
    "git_unified_zero_diff",
    "parse_unified_zero_hunks",
    "python_ast_expander",
    "resolve_code_node_version",
    "should_accept_ast_parent",
    "symbol_key",
]


def __getattr__(name: str):
    if name in {"CodeHunk", "CodeNode"}:
        from .models import CodeHunk
        from .models import CodeNode

        return {"CodeHunk": CodeHunk, "CodeNode": CodeNode}[name]
    if name in {
        "AstExpander",
        "AstExpansion",
        "AstExpansionResult",
        "code_nodes_from_hunks",
        "default_ast_expander",
        "extract_code_nodes_from_commit",
        "python_ast_expander",
        "should_accept_ast_parent",
    }:
        from .ast import AstExpander
        from .ast import AstExpansion
        from .ast import AstExpansionResult
        from .ast import code_nodes_from_hunks
        from .ast import default_ast_expander
        from .ast import extract_code_nodes_from_commit
        from .ast import python_ast_expander
        from .ast import should_accept_ast_parent

        return {
            "AstExpander": AstExpander,
            "AstExpansion": AstExpansion,
            "AstExpansionResult": AstExpansionResult,
            "code_nodes_from_hunks": code_nodes_from_hunks,
            "default_ast_expander": default_ast_expander,
            "extract_code_nodes_from_commit": extract_code_nodes_from_commit,
            "python_ast_expander": python_ast_expander,
            "should_accept_ast_parent": should_accept_ast_parent,
        }[name]
    if name in {"git_file_at_commit", "git_unified_zero_diff", "parse_unified_zero_hunks"}:
        from .diff import git_file_at_commit
        from .diff import git_unified_zero_diff
        from .diff import parse_unified_zero_hunks

        return {
            "git_file_at_commit": git_file_at_commit,
            "git_unified_zero_diff": git_unified_zero_diff,
            "parse_unified_zero_hunks": parse_unified_zero_hunks,
        }[name]
    if name in {"SymbolRecord", "symbol_key"}:
        from .symbols import SymbolRecord
        from .symbols import symbol_key

        return {"SymbolRecord": SymbolRecord, "symbol_key": symbol_key}[name]
    if name in {"CodeVersionPlan", "CodeVersionRecord", "CodeVersionRelation", "resolve_code_node_version"}:
        from .versions import CodeVersionPlan
        from .versions import CodeVersionRecord
        from .versions import CodeVersionRelation
        from .versions import resolve_code_node_version

        return {
            "CodeVersionPlan": CodeVersionPlan,
            "CodeVersionRecord": CodeVersionRecord,
            "CodeVersionRelation": CodeVersionRelation,
            "resolve_code_node_version": resolve_code_node_version,
        }[name]
    raise AttributeError(name)
