from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class GraphViewRef:
    view_id: str
    branch: str
    mode: str
    graph_commit_id: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class GraphViewStore(Protocol):
    def ensure_graph_view(self, *, branch: str = "main", mode: str = "active") -> dict[str, object]:
        ...


def resolve_graph_view(store: GraphViewStore, *, branch: str = "main", mode: str = "active") -> GraphViewRef:
    row = store.ensure_graph_view(branch=branch, mode=mode)
    return GraphViewRef(
        view_id=str(row.get("view_id") or ""),
        branch=str(row.get("branch") or branch),
        mode=str(row.get("mode") or mode),
        graph_commit_id=str(row.get("graph_commit_id") or ""),
        status=str(row.get("status") or "active"),
    )
