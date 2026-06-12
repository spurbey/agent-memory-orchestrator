from __future__ import annotations

from dataclasses import dataclass

from .identity import normalize_file_path
from .models import ResolvedAnchor
from .models import StructuralHarnessGraph


@dataclass(slots=True, frozen=True)
class ResolvedAnchors:
    resolved: tuple[ResolvedAnchor, ...]
    unresolved: tuple[str, ...]

    @property
    def has_any(self) -> bool:
        return bool(self.resolved)

    @property
    def is_complete(self) -> bool:
        return not self.unresolved


def resolve_anchors(
    graph: StructuralHarnessGraph,
    *,
    files: tuple[str, ...] = (),
    symbols: tuple[str, ...] = (),
) -> ResolvedAnchors:
    resolved: list[ResolvedAnchor] = []
    unresolved: list[str] = []
    file_nodes = tuple(graph.nodes_by_kind("File"))
    symbol_nodes = tuple(graph.nodes_by_kind("Symbol"))
    file_by_path = {
        normalize_file_path(str(node.metadata.get("path") or node.label)).lower(): node for node in file_nodes
    }
    for anchor in files:
        key = normalize_file_path(anchor).lower()
        node = file_by_path.get(key)
        reason = "exact_file_path"
        confidence = 1.0
        if node is None:
            matches = [candidate for path, candidate in file_by_path.items() if path.endswith(key)]
            if len(matches) == 1:
                node = matches[0]
                reason = "unique_file_suffix"
                confidence = 0.9
        if node is None:
            unresolved.append(f"file:{anchor}")
        else:
            resolved.append(ResolvedAnchor(requested=anchor, node_id=node.id, kind="File", confidence=confidence, reason=reason))

    for anchor in symbols:
        node = _resolve_symbol(symbol_nodes, anchor)
        if node is None:
            unresolved.append(f"symbol:{anchor}")
        else:
            resolved.append(ResolvedAnchor(requested=anchor, node_id=node.id, kind="Symbol", confidence=1.0, reason="symbol_name_match"))
    return ResolvedAnchors(resolved=tuple(resolved), unresolved=tuple(unresolved))


def _resolve_symbol(symbol_nodes: tuple[object, ...], anchor: str) -> object | None:
    safe_anchor = str(anchor or "").strip()
    if not safe_anchor:
        return None
    file_part = ""
    symbol_part = safe_anchor
    if "::" in safe_anchor:
        file_part, symbol_part = safe_anchor.rsplit("::", 1)
        file_part = normalize_file_path(file_part).lower()
    symbol_key = symbol_part.lower()
    matches = []
    for node in symbol_nodes:
        metadata = getattr(node, "metadata", {})
        qualified = str(metadata.get("qualified_name") or getattr(node, "label", "")).lower()
        path = normalize_file_path(str(metadata.get("path") or "")).lower()
        if qualified == symbol_key or qualified.endswith(f".{symbol_key}"):
            if not file_part or path == file_part or path.endswith(file_part):
                matches.append(node)
    return matches[0] if len(matches) == 1 else None


__all__ = ["ResolvedAnchors", "resolve_anchors"]
