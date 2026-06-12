from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessRuntimeService
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteHarnessGraphRepository
from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteProjectionCache


def test_sqlite_runtime_repository_reopens_bootstrapped_repo_without_rebuild(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src").mkdir()
    (repo_root / "src" / "auth.py").write_text(
        'def refresh_token():\n    """Refresh token before redirect."""\n    return True\n',
        encoding="utf-8",
    )
    db_path = tmp_path / "harness.sqlite"

    with SQLiteHarnessGraphRepository(db_path) as graph_repo:
        with SQLiteProjectionCache(db_path) as projection_cache:
            runtime = SemanticHarnessRuntimeService(
                graph_repository=graph_repo,
                projection_cache=projection_cache,
            )
            bootstrap = runtime.bootstrap_repo(repo_root, repo_id="repo:test")

    with SQLiteHarnessGraphRepository(db_path) as reopened_repo:
        with SQLiteProjectionCache(db_path) as reopened_projection_cache:
            runtime = SemanticHarnessRuntimeService(
                graph_repository=reopened_repo,
                projection_cache=reopened_projection_cache,
            )
            graph = runtime.load_graph("repo:test")
            response = runtime.query(
                "repo:test",
                HarnessQueryRequest(
                    intent="edit_plan",
                    user_goal="refresh token redirect",
                    max_cards=2,
                ),
            )

            assert graph is not None
            assert response.cards
            assert reopened_projection_cache.stats() == {"hits": 1, "misses": 0}
            assert runtime.load_graph("repo:missing") is None
            assert bootstrap.projection_document_count > 0
