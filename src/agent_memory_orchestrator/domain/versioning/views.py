from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class GraphViewRef:
    view_id: str
    repo_id: str
    branch: str
    mode: str
    graph_commit_id: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class GraphViewStore(Protocol):
    def ensure_graph_view(self, *, repo_id: str = "", branch: str = "main", mode: str = "active") -> dict[str, object]:
        ...


def resolve_graph_view(store: GraphViewStore, *, repo_id: str = "", branch: str = "main", mode: str = "active") -> GraphViewRef:
    row = store.ensure_graph_view(repo_id=repo_id, branch=branch, mode=mode)
    return GraphViewRef(
        view_id=str(row.get("view_id") or ""),
        repo_id=str(row.get("repo_id") or repo_id),
        branch=str(row.get("branch") or branch),
        mode=str(row.get("mode") or mode),
        graph_commit_id=str(row.get("graph_commit_id") or ""),
        status=str(row.get("status") or "active"),
    )


def graph_view_id(*, repo_id: str = "", branch: str = "main", mode: str = "active") -> str:
    safe_repo = _safe_part(repo_id)[:72] if repo_id else ""
    if safe_repo:
        return f"v2view:{safe_repo}:{_safe_part(branch)}:{_safe_part(mode)}"
    return f"v2view:{_safe_part(branch)}:{_safe_part(mode)}"


def _safe_part(value: str) -> str:
    out = []
    for ch in str(value):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "value"
