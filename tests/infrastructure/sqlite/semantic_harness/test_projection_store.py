from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import StructuralHarnessService
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_projection_set
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteProjectionCache


def test_sqlite_projection_cache_round_trips_projection_set(tmp_path) -> None:
    db_path = tmp_path / "harness.sqlite"
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/auth.py", text='"""Auth module."""\n\ndef login(user):\n    return user.is_active\n'),),
    )
    projection = build_projection_set(graph)

    with SQLiteProjectionCache(db_path) as cache:
        cache.save(projection)

    with SQLiteProjectionCache(db_path) as reopened:
        persisted = reopened.get(projection.projection_id)

        assert persisted is not None
        assert persisted.projection_id == projection.projection_id
        assert persisted.graph_snapshot_id == projection.graph_snapshot_id
        assert persisted.document_ids_hash == projection.document_ids_hash
        assert {document.doc_id for document in persisted.documents} == {document.doc_id for document in projection.documents}
        assert {document.content_hash for document in persisted.documents} == {document.content_hash for document in projection.documents}


def test_sqlite_projection_cache_get_or_build_reuses_persisted_set(tmp_path) -> None:
    db_path = tmp_path / "harness.sqlite"
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/auth.py", text="def login(user):\n    return user.is_active\n"),),
    )

    with SQLiteProjectionCache(db_path) as first_cache:
        first = first_cache.get_or_build(graph)
        assert first_cache.stats() == {"hits": 0, "misses": 1}

    with SQLiteProjectionCache(db_path) as second_cache:
        second = second_cache.get_or_build(graph)

        assert second.projection_id == first.projection_id
        assert second_cache.stats() == {"hits": 1, "misses": 0}


def test_sqlite_projection_cache_integrates_with_structural_service(tmp_path) -> None:
    db_path = tmp_path / "harness.sqlite"
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/auth.py", text="def login(user):\n    return user.is_active\n"),),
    )

    with SQLiteProjectionCache(db_path) as cache:
        service = StructuralHarnessService(projection_cache=cache)
        response = service.query(
            graph,
            HarnessQueryRequest(
                intent="edit_plan",
                user_goal="fix login behavior",
                symbols=("login",),
            ),
        )

        assert response.cards
        assert cache.get(service.projection_set(graph).projection_id) is not None
