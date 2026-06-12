from __future__ import annotations

import re
from collections.abc import Iterable

from .doc_semantics import add_doc_semantics
from .identity import code_region_id
from .identity import file_id
from .identity import normalize_file_path
from .models import HarnessEdge
from .models import HarnessNode
from .models import SourceFile
from .models import StructuralHarnessGraph
from .python_structure import add_python_structure
from .versions import add_baseline_versions
from .web_structure import JS_TS_LANGUAGES
from .web_structure import add_web_structure


def build_structural_graph(repo_id: str, files: Iterable[SourceFile]) -> StructuralHarnessGraph:
    nodes: dict[str, HarnessNode] = {
        repo_id: HarnessNode(id=repo_id, kind="Repo", label=repo_id, repo_id=repo_id)
    }
    edges: list[HarnessEdge] = []
    sources = tuple(files)
    source_paths = tuple(normalize_file_path(source.path) for source in sources)
    file_node_ids: dict[str, str] = {}
    for source in sources:
        path = normalize_file_path(source.path)
        file_node_id = file_id(repo_id, path)
        file_node_ids[path] = file_node_id
        language = source.language or _language_for_path(path)
        nodes[file_node_id] = HarnessNode(
            id=file_node_id,
            kind="File",
            label=path,
            repo_id=repo_id,
            summary=_file_summary(source.text),
            metadata={"path": path, "language": language, "line_count": _line_count(source.text)},
        )
        edges.append(HarnessEdge(source_id=repo_id, target_id=file_node_id, kind="CONTAINS"))
    for source in sources:
        path = normalize_file_path(source.path)
        file_node_id = file_node_ids[path]
        language = source.language or _language_for_path(path)
        if language == "python":
            add_python_structure(
                repo_id=repo_id,
                source=source,
                file_node_id=file_node_id,
                all_python_paths=tuple(path for path in source_paths if path.endswith(".py")),
                nodes=nodes,
                edges=edges,
            )
        elif language == "markdown":
            _add_markdown_regions(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges)
        elif language in {"json", "toml", "yaml"}:
            _add_config_region(repo_id=repo_id, source=source, file_node_id=file_node_id, nodes=nodes, edges=edges, language=language)
        elif language in JS_TS_LANGUAGES or language in {"css", "html"}:
            add_web_structure(
                repo_id=repo_id,
                source=source,
                file_node_id=file_node_id,
                language=language,
                nodes=nodes,
                edges=edges,
            )
    add_doc_semantics(repo_id=repo_id, sources=sources, nodes=nodes, edges=edges)
    add_baseline_versions(nodes=nodes, edges=edges)
    return StructuralHarnessGraph(repo_id=repo_id, nodes=tuple(nodes.values()), edges=tuple(edges))


def _add_markdown_regions(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    path = normalize_file_path(source.path)
    for idx, line in enumerate(source.text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = match.group(2).strip()
        region_key = f"heading:{_slug(title)}:{idx}"
        node_id = code_region_id(repo_id, path, region_key, "markdown_section")
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="CodeRegion",
            label=title,
            repo_id=repo_id,
            summary=title,
            metadata={"path": path, "region_kind": "markdown_section", "line_start": idx, "line_end": idx},
        )
        edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS"))


def _add_config_region(
    *,
    repo_id: str,
    source: SourceFile,
    file_node_id: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
    language: str,
) -> None:
    if not source.text.strip():
        return
    path = normalize_file_path(source.path)
    node_id = code_region_id(repo_id, path, "whole_file", "config_stanza")
    nodes[node_id] = HarnessNode(
        id=node_id,
        kind="CodeRegion",
        label=f"{path} config",
        repo_id=repo_id,
        summary=_file_summary(source.text),
        metadata={"path": path, "region_kind": "config_stanza", "language": language, "line_start": 1, "line_end": _line_count(source.text)},
    )
    edges.append(HarnessEdge(source_id=file_node_id, target_id=node_id, kind="CONTAINS"))


def _language_for_path(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python",
        "md": "markdown",
        "json": "json",
        "toml": "toml",
        "yaml": "yaml",
        "yml": "yaml",
        "js": "js",
        "jsx": "jsx",
        "ts": "ts",
        "tsx": "tsx",
        "css": "css",
        "html": "html",
    }.get(suffix, suffix)


def _line_count(text: str) -> int:
    return len(text.splitlines()) or 1


def _file_summary(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "section"


__all__ = ["build_structural_graph"]
