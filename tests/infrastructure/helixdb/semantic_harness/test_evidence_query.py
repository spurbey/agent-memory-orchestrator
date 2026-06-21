from __future__ import annotations

from types import SimpleNamespace

from agent_memory_orchestrator.domain.semantic_harness import EdgeExpansion
from agent_memory_orchestrator.domain.semantic_harness import GraphSeed
from agent_memory_orchestrator.domain.semantic_harness import GraphSlicePlan
from agent_memory_orchestrator.domain.semantic_harness.identity import file_id
from agent_memory_orchestrator.infrastructure.helixdb.semantic_harness.evidence_query import HelixEvidenceQuery


class _CountingClient:
    def __init__(self, paths: tuple[str, ...]) -> None:
        self.config = SimpleNamespace(batch_size=250)
        self.calls = 0
        self._paths = paths

    def send(self, _request):
        self.calls += 1
        if self.calls == 1:
            return {
                "nodes": {
                    "properties": [
                        {
                            "node_id": file_id("repo:test", path),
                            "kind": "File",
                            "label": path,
                            "repo_id": "repo:test",
                            "path": path,
                            "metadata_json": f'{{"path":"{path}"}}',
                        }
                        for path in self._paths
                    ]
                }
            }
        return {"neighbors": {"properties": []}, "edges": {"properties": []}}


def test_candidate_resolution_and_expansion_are_batched_by_kind() -> None:
    paths = tuple(f"src/file_{index}.py" for index in range(100))
    client = _CountingClient(paths)
    plan = GraphSlicePlan(
        repo_id="repo:test",
        purpose="rank_tool_hits",
        seeds=tuple(GraphSeed(kind="file", value=path) for path in paths),
        expansions=(
            EdgeExpansion(kind="DEFINES", direction="outgoing"),
            EdgeExpansion(kind="CONTAINS", direction="outgoing"),
        ),
        max_nodes=2_000,
        max_edges=4_000,
    )

    graph = HelixEvidenceQuery(client).execute(plan)  # type: ignore[arg-type]

    assert client.calls == 3
    assert len(graph.nodes) == 100
    assert graph.edges == ()
