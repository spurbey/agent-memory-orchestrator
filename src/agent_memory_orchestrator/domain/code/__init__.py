"""Code analysis and code identity domain contracts."""

from __future__ import annotations

from .ast import AstExpander
from .ast import AstExpansion
from .ast import AstExpansionResult
from .ast import code_nodes_from_hunks
from .ast import default_ast_expander
from .ast import extract_code_nodes_from_commit
from .ast import should_accept_ast_parent
from .diff import git_file_at_commit
from .diff import git_unified_zero_diff
from .diff import parse_unified_zero_hunks
from .symbols import SymbolRecord
from .symbols import symbol_key
from .versions import CodeVersionPlan
from .versions import CodeVersionRecord
from .versions import CodeVersionRelation
from .versions import resolve_code_node_version
from .models import CodeHunk
from .models import CodeNode

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
    "resolve_code_node_version",
    "should_accept_ast_parent",
    "symbol_key",
]
