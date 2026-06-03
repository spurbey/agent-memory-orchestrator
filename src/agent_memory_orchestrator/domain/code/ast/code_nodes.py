from __future__ import annotations

from pathlib import Path

from ..diff import git_file_at_commit
from ..diff import git_unified_zero_diff
from ..diff import parse_unified_zero_hunks
from ..models import CodeHunk
from ..models import CodeNode
from .dispatch import default_ast_expander
from .models import AstExpander
from .models import AstExpansion
from .models import AstExpansionResult
from .utils import _hunk_line_range
from .utils import _language_for_file
from .utils import _patch_changed_lines
from .utils import _previous_snippet
from .utils import _snippet
from .utils import _structural_id
from .utils import should_accept_ast_parent


def code_nodes_from_hunks(
    hunks: list[CodeHunk],
    *,
    file_contents: dict[str, str],
    old_file_contents: dict[str, str] | None = None,
    ast_expander: AstExpander | None = None,
) -> list[CodeNode]:
    nodes: list[CodeNode] = []
    for hunk in hunks:
        lines = file_contents.get(hunk.file_path, "").splitlines()
        old_lines = (old_file_contents or {}).get(hunk.file_path, "").splitlines()
        expanded = _normalize_expansions(ast_expander(hunk, lines) if ast_expander is not None else None)
        if not expanded:
            start, end = _hunk_line_range(hunk, len(lines))
            expanded = (
                AstExpansion(
                    ast_type="unparsed_hunk",
                    line_start=start,
                    line_end=end,
                    content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
                    ast_status="unparsed",
                    diagnostics=("ast_expander_missing",),
                ),
            )
        for item in expanded:
            start, end = item.line_start, item.line_end
            content = item.content
            ast_type = item.ast_type
            ast_status = item.ast_status
            diagnostics = list(item.diagnostics)
            if not should_accept_ast_parent(max(1, hunk.new_count), max(1, end - start + 1)):
                start, end = _hunk_line_range(hunk, len(lines))
                content = _snippet(lines, start, end) or _patch_changed_lines(hunk.patch)
                ast_type = "unparsed_hunk"
                ast_status = "unparsed_parent_too_large"
                diagnostics.append("parent_size_gt_3x_hunk")
            prev_content = _previous_snippet(old_lines, hunk)
            metadata = {
                "hunk_id": hunk.id,
                "hunk_old_start": hunk.old_start,
                "hunk_old_count": hunk.old_count,
                "hunk_new_start": hunk.new_start,
                "hunk_new_count": hunk.new_count,
                "language": item.language or _language_for_file(hunk.file_path),
                "symbol_name": item.symbol_name,
                "symbol_kind": item.symbol_kind,
                "structural_id": item.structural_id
                or _structural_id(hunk.file_path, item.ast_type, item.symbol_name, start, end),
                "ast_diagnostics": diagnostics,
            }
            if item.metadata:
                metadata.update(item.metadata)
            nodes.append(
                CodeNode(
                    id=f"code:{hunk.commit_id}:{hunk.file_path}:{start}:{end}",
                    session_id=hunk.session_id,
                    extraction_run_id=hunk.extraction_run_id,
                    file_path=hunk.file_path,
                    ast_type=ast_type,
                    line_start=start,
                    line_end=end,
                    content=content,
                    prev_content=prev_content,
                    commit_id=hunk.commit_id,
                    evidence_ids=hunk.evidence_ids,
                    ast_status=ast_status,
                    metadata=metadata,
                )
            )
    return nodes


def extract_code_nodes_from_commit(
    *,
    repo_root: Path,
    commit: str,
    session_id: str,
    extraction_run_id: str,
    evidence_ids: tuple[str, ...],
    file_path: str = "",
    ast_expander: AstExpander | None = None,
) -> tuple[list[CodeHunk], list[CodeNode]]:
    diff_text = git_unified_zero_diff(repo_root, commit, file_path=file_path)
    hunks = parse_unified_zero_hunks(
        diff_text,
        session_id=session_id,
        extraction_run_id=extraction_run_id,
        commit_id=commit,
        evidence_ids=evidence_ids,
    )
    contents = {hunk.file_path: git_file_at_commit(repo_root, commit, hunk.file_path) for hunk in hunks}
    old_contents = {
        hunk.file_path: git_file_at_commit(repo_root, f"{commit}^", hunk.file_path)
        for hunk in hunks
    }
    expander = ast_expander if ast_expander is not None else default_ast_expander
    return hunks, code_nodes_from_hunks(
        hunks,
        file_contents=contents,
        old_file_contents=old_contents,
        ast_expander=expander,
    )

def _normalize_expansions(expanded: AstExpansionResult | None) -> tuple[AstExpansion, ...]:
    if expanded is None:
        return ()
    if isinstance(expanded, AstExpansion):
        return (expanded,)
    if isinstance(expanded, tuple) and len(expanded) == 4 and isinstance(expanded[0], str):
        ast_type, start, end, content = expanded
        return (AstExpansion(ast_type=ast_type, line_start=int(start), line_end=int(end), content=str(content)),)
    return tuple(item for item in expanded if isinstance(item, AstExpansion))


__all__ = ["code_nodes_from_hunks", "extract_code_nodes_from_commit"]
