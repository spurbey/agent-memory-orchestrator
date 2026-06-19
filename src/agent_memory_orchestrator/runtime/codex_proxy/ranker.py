"""Proxy ranker adapter.

Owns: convert captured rg output into RankToolHitsResult.
Does not own HTTP transport, payload mutation, raw storage, or graph bootstrap.

The adapter intentionally depends on the Semantic Harness store boundary. Today
that store is SQLite; a later HelixDB backend should replace the load helpers
without changing proxy mutation or the rank_tool_hits scoring contract.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.projection import HarnessProjectionDocument
from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_rank_tool_hits
from agent_memory_orchestrator.runtime.codex_proxy.tool_outputs import CapturedProxyToolOutput


RankFn = Callable[[CapturedProxyToolOutput], RankToolHitsResult | None]


@dataclass(frozen=True)
class ProxyRankerConfig:
    repo_id: str
    repo_root: Path | None = None
    max_results: int = 5
    db_path: Path | None = None
    user_goal: str = ""


class ProxyRankToolHitsAdapter:
    """Adapts CapturedProxyToolOutput into a RankToolHitsResult.

    Pass rank_fn in tests to avoid a real warmed graph. In production, pass
    config with a repo_id whose graph was already bootstrapped.
    """

    def __init__(
        self,
        config: ProxyRankerConfig | None = None,
        *,
        rank_fn: RankFn | None = None,
    ) -> None:
        self._config = config
        self._rank_fn = rank_fn
        self._graph_cache: dict[str, StructuralHarnessGraph] = {}
        self._projection_cache: dict[str, tuple[HarnessProjectionDocument, ...]] = {}

    def rank(self, captured: CapturedProxyToolOutput) -> RankToolHitsResult | None:
        """Return ranked hits or None. Caller must fail open on None."""
        try:
            if self._rank_fn is not None:
                return self._rank_fn(captured)
            return self._rank_via_graph(captured)
        except Exception:
            return None

    def _rank_via_graph(self, captured: CapturedProxyToolOutput) -> RankToolHitsResult | None:
        if self._config is None:
            return None
        repo_id = self._config.repo_id.strip()
        if not repo_id:
            return None

        graph = self._load_graph(repo_id)
        if graph is None:
            return None
        projection_documents = self._load_projection_documents(repo_id, graph)

        recent_tool_result = {
            "kind": "rg",
            "text": captured.output,
            "raw_ref": captured.raw_ref,
            "user_prompt": self._config.user_goal,
        }
        result = answer_rank_tool_hits(
            graph,
            user_goal=self._config.user_goal,
            recent_tool_result=recent_tool_result,
            max_results=self._config.max_results,
            projection_documents=projection_documents,
        )
        return result if result.ranked_hits else None

    def _load_graph(self, repo_id: str) -> StructuralHarnessGraph | None:
        cached = self._graph_cache.get(repo_id)
        if cached is not None:
            return cached
        from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteHarnessGraphRepository

        with SQLiteHarnessGraphRepository(self._db_path()) as graph_repository:
            store = graph_repository.load(repo_id)
            if store is None:
                return None
            graph = store.to_graph()
        self._graph_cache[repo_id] = graph
        return graph

    def _load_projection_documents(
        self,
        repo_id: str,
        graph: StructuralHarnessGraph,
    ) -> tuple[HarnessProjectionDocument, ...]:
        cached = self._projection_cache.get(repo_id)
        if cached is not None:
            return cached
        from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteProjectionCache

        with SQLiteProjectionCache(self._db_path()) as projection_cache:
            documents = projection_cache.get_or_build(graph).documents
        self._projection_cache[repo_id] = documents
        return documents

    def _db_path(self) -> Path:
        if self._config is not None and self._config.db_path is not None:
            return self._config.db_path.expanduser().resolve()
        raw = os.environ.get("AMO_PROXY_SEMANTIC_HARNESS_DB_PATH", "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        settings = Settings.load()
        return (settings.home / ".data" / "semantic_harness.sqlite").resolve()


def make_ranker_from_env() -> ProxyRankToolHitsAdapter:
    """Build a ranker from env. Missing repo_id still fails open at rank time."""
    repo_id = os.environ.get("AMO_PROXY_REPO_ID", "").strip()
    raw_db_path = os.environ.get("AMO_PROXY_SEMANTIC_HARNESS_DB_PATH", "").strip()
    db_path = Path(raw_db_path).expanduser().resolve() if raw_db_path else None
    user_goal = os.environ.get("AMO_PROXY_USER_GOAL", "").strip()
    config = ProxyRankerConfig(repo_id=repo_id, db_path=db_path, user_goal=user_goal)
    return ProxyRankToolHitsAdapter(config=config)


__all__ = ["ProxyRankerConfig", "ProxyRankToolHitsAdapter", "RankFn", "make_ranker_from_env"]
