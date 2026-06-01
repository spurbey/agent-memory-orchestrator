"""Compatibility facade for code diff and AST analysis contracts."""

from __future__ import annotations

from .ast import AstExpander
from .ast import AstExpansion
from .ast import AstExpansionResult
from .ast import brace_language_expander
from .ast import code_nodes_from_hunks
from .ast import css_rule_expander
from .ast import default_ast_expander
from .ast import extract_code_nodes_from_commit
from .ast import markdown_section_expander
from .ast import markup_expander
from .ast import python_ast_expander
from .ast import should_accept_ast_parent
from .ast import structured_config_expander
from .diff import git_file_at_commit
from .diff import git_unified_zero_diff
from .diff import parse_unified_zero_hunks

__all__ = [
    "AstExpander",
    "AstExpansion",
    "AstExpansionResult",
    "brace_language_expander",
    "code_nodes_from_hunks",
    "css_rule_expander",
    "default_ast_expander",
    "extract_code_nodes_from_commit",
    "git_file_at_commit",
    "git_unified_zero_diff",
    "markdown_section_expander",
    "markup_expander",
    "parse_unified_zero_hunks",
    "python_ast_expander",
    "should_accept_ast_parent",
    "structured_config_expander",
]
