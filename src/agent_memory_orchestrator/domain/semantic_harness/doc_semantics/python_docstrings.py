from __future__ import annotations

import ast

from ..identity import docstring_id
from ..identity import normalize_file_path
from ..identity import symbol_id
from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import SourceFile
from .models import DocSemanticArtifact


def extract_python_docstrings(
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    node_ids: set[str],
) -> tuple[tuple[DocSemanticArtifact, HarnessEdge], ...]:
    path = normalize_file_path(source.path)
    if (source.language or "").lower() != "python" and not path.endswith(".py"):
        return ()
    try:
        tree = ast.parse(source.text)
    except SyntaxError:
        return ()

    artifacts: list[tuple[DocSemanticArtifact, HarnessEdge]] = []
    module_doc = _docstring_record(tree)
    if module_doc:
        artifacts.append(_module_docstring(repo_id=repo_id, path=path, file_node_id=file_node_id, record=module_doc))

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[tuple[str, str]] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            self._add_docstring(node=node, symbol_kind="class")
            self.stack.append((node.name, "class"))
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._visit_function(node)

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            symbol_kind = "method" if any(kind == "class" for _name, kind in self.stack) else "function"
            self._add_docstring(node=node, symbol_kind=symbol_kind)
            self.stack.append((node.name, symbol_kind))
            self.generic_visit(node)
            self.stack.pop()

        def _add_docstring(
            self,
            *,
            node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            symbol_kind: str,
        ) -> None:
            record = _docstring_record(node)
            if not record:
                return
            qualified = ".".join([*(name for name, _kind in self.stack), node.name])
            target_node_id = symbol_id(repo_id, path, qualified, symbol_kind)
            if target_node_id not in node_ids:
                return
            artifacts.append(
                _symbol_docstring(
                    repo_id=repo_id,
                    path=path,
                    file_node_id=file_node_id,
                    qualified_name=qualified,
                    symbol_kind=symbol_kind,
                    target_node_id=target_node_id,
                    record=record,
                )
            )

    Visitor().visit(tree)
    return tuple(artifacts)


def _module_docstring(
    *,
    repo_id: str,
    path: str,
    file_node_id: str,
    record: tuple[str, int, int],
) -> tuple[DocSemanticArtifact, HarnessEdge]:
    text, line_start, line_end = record
    node_id = docstring_id(repo_id, path, "module", "module")
    return (
        DocSemanticArtifact(
            node=HarnessNode(
                id=node_id,
                kind="DocString",
                label=f"{path} module docstring",
                repo_id=repo_id,
                summary=_summary(text),
                metadata={
                    "path": path,
                    "doc_kind": "module_docstring",
                    "target_node_id": file_node_id,
                    "target_kind": "File",
                    "line_start": line_start,
                    "line_end": line_end,
                    "content_excerpt": text[:600],
                },
            ),
            text=text,
            source_file_id=file_node_id,
        ),
        HarnessEdge(source_id=node_id, target_id=file_node_id, kind="DOCUMENTS_FILE", confidence=0.95),
    )


def _symbol_docstring(
    *,
    repo_id: str,
    path: str,
    file_node_id: str,
    qualified_name: str,
    symbol_kind: str,
    target_node_id: str,
    record: tuple[str, int, int],
) -> tuple[DocSemanticArtifact, HarnessEdge]:
    text, line_start, line_end = record
    node_id = docstring_id(repo_id, path, qualified_name, symbol_kind)
    return (
        DocSemanticArtifact(
            node=HarnessNode(
                id=node_id,
                kind="DocString",
                label=f"{qualified_name} docstring",
                repo_id=repo_id,
                summary=_summary(text),
                metadata={
                    "path": path,
                    "doc_kind": "symbol_docstring",
                    "target_node_id": target_node_id,
                    "target_kind": "Symbol",
                    "qualified_name": qualified_name,
                    "symbol_kind": symbol_kind,
                    "line_start": line_start,
                    "line_end": line_end,
                    "content_excerpt": text[:600],
                },
            ),
            text=text,
            source_file_id=file_node_id,
        ),
        HarnessEdge(source_id=node_id, target_id=target_node_id, kind="DOCUMENTS_SYMBOL", confidence=0.96),
    )


def _docstring_record(node: ast.AST) -> tuple[str, int, int] | None:
    text = ast.get_docstring(node)
    if not text:
        return None
    first = getattr(node, "body", [None])[0] if getattr(node, "body", None) else None
    line_start = int(getattr(first, "lineno", getattr(node, "lineno", 1)) or 1)
    line_end = int(getattr(first, "end_lineno", line_start) or line_start)
    return text.strip(), line_start, line_end


def _summary(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return ""


__all__ = ["extract_python_docstrings"]
