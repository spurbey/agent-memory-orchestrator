"""Tests for runtime/codex_proxy/ranker.py"""

from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import RepoBootstrapOptions
from agent_memory_orchestrator.application.services.semantic_harness import InMemoryHarnessGraphRepository
from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessRuntimeService
from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult
from agent_memory_orchestrator.runtime.codex_proxy.ranker import ProxyRankerConfig
from agent_memory_orchestrator.runtime.codex_proxy.ranker import ProxyRankToolHitsAdapter
from agent_memory_orchestrator.runtime.codex_proxy.ranker import make_ranker_from_env
from agent_memory_orchestrator.runtime.codex_proxy.tool_outputs import CapturedProxyToolOutput


def _captured(output: str = "src/a.py:10:def foo():") -> CapturedProxyToolOutput:
    return CapturedProxyToolOutput(
        item_type="local_shell_call_output",
        call_id="call_1",
        output=output,
        raw_ref="sha256:" + "a" * 64,
    )


def _ok_result() -> RankToolHitsResult:
    from agent_memory_orchestrator.domain.semantic_harness.query_modes.rank_tool_hits import (
        RankedToolHit,
        RankedToolLine,
    )
    return RankToolHitsResult(
        status="ready",
        ranked_hits=(
            RankedToolHit(
                path="src/a.py",
                file_node_id="node_1",
                score=0.9,
                match_count=1,
                line_refs=(RankedToolLine(file_path="src/a.py", line=10, text="def foo():"),),
                symbol_node_ids=(),
                semantic_similarity=0.8,
                semantic_doc_ids=(),
                reason_codes=("rg_match_strength:0.90",),
            ),
        ),
        query_text="",
        raw_ref="sha256:" + "a" * 64,
        embedding_backend="hash_token_char_cosine_v1",
        warnings=(),
    )


def test_injected_rank_fn_is_called():
    result = _ok_result()
    calls = []

    def stub(captured):
        calls.append(captured)
        return result

    adapter = ProxyRankToolHitsAdapter(rank_fn=stub)
    out = adapter.rank(_captured())
    assert out is result
    assert len(calls) == 1


def test_missing_repo_id_returns_none():
    adapter = ProxyRankToolHitsAdapter(config=ProxyRankerConfig(repo_id=""))
    assert adapter.rank(_captured()) is None


def test_graph_unavailable_returns_none(monkeypatch):
    """If load_graph fails, rank returns None."""
    import agent_memory_orchestrator.runtime.codex_proxy.ranker as _mod

    def _fail(self, repo_id):
        raise RuntimeError("kuzu not available")

    monkeypatch.setattr(_mod.ProxyRankToolHitsAdapter, "_load_graph", _fail)
    adapter = ProxyRankToolHitsAdapter(config=ProxyRankerConfig(repo_id="some-repo"))
    assert adapter.rank(_captured()) is None


def test_rank_fn_exception_is_not_propagated():
    def boom(captured):
        raise ValueError("unexpected error")

    adapter = ProxyRankToolHitsAdapter(rank_fn=boom)
    assert adapter.rank(_captured()) is None


def test_rank_fn_returning_none_passes_through():
    adapter = ProxyRankToolHitsAdapter(rank_fn=lambda c: None)
    assert adapter.rank(_captured()) is None


def test_make_ranker_from_env_reads_repo_and_goal(monkeypatch):
    monkeypatch.setenv("AMO_PROXY_REPO_ID", "repo:test")
    monkeypatch.setenv("AMO_PROXY_USER_GOAL", "rank snapshot Helix hits")

    adapter = make_ranker_from_env()

    assert adapter._config == ProxyRankerConfig(  # noqa: SLF001 - config is adapter state under test
        repo_id="repo:test",
        user_goal="rank snapshot Helix hits",
    )


def test_ranker_scores_a_warmed_graph_through_repository_boundary(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "snapshots.py").write_text(
        'def graph_snapshot_identity(graph):\n    """Return a structural graph snapshot id."""\n    return "snapshot"\n',
        encoding="utf-8",
    )
    repo_id = "repo:proxy-ranker-test"
    graph_repository = InMemoryHarnessGraphRepository()
    runtime = SemanticHarnessRuntimeService(graph_repository=graph_repository)
    runtime.bootstrap_repo(
        repo_root,
        repo_id=repo_id,
        options=RepoBootstrapOptions(prefer_git_tracked=False),
    )
    graph = graph_repository.load(repo_id).to_graph()

    adapter = ProxyRankToolHitsAdapter(
        config=ProxyRankerConfig(
            repo_id=repo_id,
            user_goal="snapshot identity",
        )
    )
    monkeypatch.setattr(adapter, "_load_graph", lambda _repo_id, **_kwargs: graph)
    result = adapter.rank(_captured("src/snapshots.py:1:def graph_snapshot_identity(graph):"))

    assert result is not None
    assert result.ranked_hits
    assert result.ranked_hits[0].path == "src/snapshots.py"
