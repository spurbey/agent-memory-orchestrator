from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import InMemoryProjectionCache
from agent_memory_orchestrator.application.services.semantic_harness import StructuralHarnessService
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import SourceFile


def test_projection_cache_reuses_projection_for_same_structural_graph() -> None:
    cache = InMemoryProjectionCache()
    service = StructuralHarnessService(projection_cache=cache)
    graph = service.bootstrap(
        repo_id="repo:test",
        files=(SourceFile(path="src/main.py", text="def run():\n    return True\n"),),
    )

    first = service.projection_set(graph)
    second = service.projection_set(graph)

    assert first is second
    assert cache.stats().size == 1
    assert cache.stats().misses == 1
    assert cache.stats().hits == 1


def test_projection_cache_invalidates_when_structural_graph_changes() -> None:
    cache = InMemoryProjectionCache()
    service = StructuralHarnessService(projection_cache=cache)
    first_graph = service.bootstrap(
        repo_id="repo:test",
        files=(SourceFile(path="src/main.py", text="def run():\n    return True\n"),),
    )
    second_graph = service.bootstrap(
        repo_id="repo:test",
        files=(
            SourceFile(path="src/main.py", text="def run():\n    return True\n"),
            SourceFile(path="src/extra.py", text="def extra():\n    return True\n"),
        ),
    )

    first = service.projection_set(first_graph)
    second = service.projection_set(second_graph)

    assert first.projection_id != second.projection_id
    assert first.graph_snapshot_id != second.graph_snapshot_id
    assert cache.stats().size == 2
    assert cache.stats().misses == 2


def test_service_query_uses_projection_cache_lazily() -> None:
    cache = InMemoryProjectionCache()
    service = StructuralHarnessService(projection_cache=cache)
    graph = service.bootstrap(
        repo_id="repo:test",
        files=(SourceFile(path="src/auth.py", text='def refresh_token():\n    """Refresh token."""\n    return True\n'),),
    )

    exact = service.query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect auth",
            files=("src/auth.py",),
            max_cards=1,
        ),
    )
    first_vague = service.query(
        graph,
        HarnessQueryRequest(intent="edit_plan", user_goal="refresh token", max_cards=1),
    )
    second_vague = service.query(
        graph,
        HarnessQueryRequest(intent="edit_plan", user_goal="refresh token", max_cards=1),
    )

    assert exact.cards[0].type == "next_file"
    assert first_vague.cards[0].title == "Inspect refresh_token"
    assert second_vague.cards[0].title == "Inspect refresh_token"
    assert cache.stats().size == 1
    assert cache.stats().misses == 1
    assert cache.stats().hits == 1
