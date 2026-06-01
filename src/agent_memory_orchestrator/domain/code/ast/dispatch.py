from __future__ import annotations

from ..models import CodeHunk
from .generic import brace_language_expander
from .generic import css_rule_expander
from .generic import markdown_section_expander
from .generic import markup_expander
from .generic import structured_config_expander
from .models import AstExpansion
from .python import python_ast_expander
from .utils import _is_brace_language
from .utils import _is_css_file
from .utils import _is_markup_file
from .utils import _is_structured_config_file


def default_ast_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    if hunk.file_path.endswith(".py"):
        return python_ast_expander(hunk, lines)
    if _is_brace_language(hunk.file_path):
        return brace_language_expander(hunk, lines)
    if _is_css_file(hunk.file_path):
        return css_rule_expander(hunk, lines)
    if _is_markup_file(hunk.file_path):
        return markup_expander(hunk, lines)
    if _is_structured_config_file(hunk.file_path):
        return structured_config_expander(hunk, lines)
    if hunk.file_path.endswith((".md", ".mdx")):
        return markdown_section_expander(hunk, lines)
    return None


__all__ = ["default_ast_expander"]
