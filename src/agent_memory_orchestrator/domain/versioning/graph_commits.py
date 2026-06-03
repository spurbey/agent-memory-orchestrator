"""GraphCommit domain contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class GraphCommitRef:
    graph_commit_id: str
    parent_graph_commit_id: str
    repo_id: str
    branch: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def graph_commit_id_for_plan(*, plan_id: str, input_graph_hash: str) -> str:
    seed = {"plan_id": str(plan_id or ""), "input_graph_hash": str(input_graph_hash or "")}
    return f"v2gcommit:{hashlib.sha256(json.dumps(seed, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:32]}"


def graph_commit_ref_from_row(row: dict[str, object]) -> GraphCommitRef:
    return GraphCommitRef(
        graph_commit_id=str(row.get("graph_commit_id") or row.get("id") or ""),
        parent_graph_commit_id=str(row.get("parent_graph_commit_id") or ""),
        repo_id=str(row.get("repo_id") or ""),
        branch=str(row.get("branch") or "main"),
        status=str(row.get("status") or ""),
    )


__all__ = ["GraphCommitRef", "graph_commit_id_for_plan", "graph_commit_ref_from_row"]
