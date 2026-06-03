from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..models import CodeHunk


@dataclass(slots=True, frozen=True)
class AstExpansion:
    ast_type: str
    line_start: int
    line_end: int
    content: str
    ast_status: str = "parsed"
    language: str = ""
    symbol_name: str = ""
    symbol_kind: str = ""
    structural_id: str = ""
    diagnostics: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


AstExpansionResult = AstExpansion | tuple[str, int, int, str] | list[AstExpansion] | tuple[AstExpansion, ...]
AstExpander = Callable[[CodeHunk, list[str]], AstExpansionResult | None]


__all__ = ["AstExpander", "AstExpansion", "AstExpansionResult"]
