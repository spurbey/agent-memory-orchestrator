"""Proxy ranker adapter.

Owns: convert captured rg output into RankToolHitsResult.
Does not own HTTP transport, payload mutation, raw storage, or graph bootstrap.

The adapter intentionally depends on the Semantic Harness store boundary.
Production loads the warmed graph from HelixDB without changing proxy mutation
or the rank_tool_hits scoring contract.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from agent_memory_orchestrator.application.services.semantic_harness.runtime.query_planner import plan_query_evidence
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_rank_tool_hits
from agent_memory_orchestrator.runtime.codex_proxy.tool_outputs import CapturedProxyToolOutput


RankFn = Callable[[CapturedProxyToolOutput], RankToolHitsResult | None]


@dataclass(frozen=True)
class ProxyRankerConfig:
    repo_id: str
    max_results: int = 5
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

        recent_tool_result = {
            "kind": "rg",
            "text": captured.output,
            "raw_ref": captured.raw_ref,
            "user_prompt": self._config.user_goal,
        }
        graph = self._load_graph(repo_id, recent_tool_result=recent_tool_result)
        if graph is None:
            return None
        from agent_memory_orchestrator.domain.semantic_harness import build_projection_set

        projection_documents = build_projection_set(graph).documents
        result = answer_rank_tool_hits(
            graph,
            user_goal=self._config.user_goal,
            recent_tool_result=recent_tool_result,
            max_results=self._config.max_results,
            projection_documents=projection_documents,
        )
        return result if result.ranked_hits else None

    def _load_graph(
        self,
        repo_id: str,
        *,
        recent_tool_result: dict[str, object],
    ) -> StructuralHarnessGraph | None:
        from agent_memory_orchestrator.infrastructure.helixdb.semantic_harness import HelixHarnessGraphRepository

        request = HarnessQueryRequest(
            intent="rank_tool_hits",
            mode="rank_tool_hits",
            user_goal=self._config.user_goal if self._config is not None else "",
            recent_tool_result=recent_tool_result,
            max_cards=self._config.max_results if self._config is not None else 5,
        )
        plan = plan_query_evidence(repo_id, request, mode="rank_tool_hits")
        if plan is None:
            return None
        with HelixHarnessGraphRepository() as graph_repository:
            return graph_repository.query_evidence(plan)

def make_ranker_from_env() -> ProxyRankToolHitsAdapter:
    """Build a ranker from env. Missing repo_id still fails open at rank time."""
    repo_id = os.environ.get("AMO_PROXY_REPO_ID", "").strip()
    user_goal = os.environ.get("AMO_PROXY_USER_GOAL", "").strip()
    config = ProxyRankerConfig(repo_id=repo_id, user_goal=user_goal)
    return ProxyRankToolHitsAdapter(config=config)


__all__ = ["ProxyRankerConfig", "ProxyRankToolHitsAdapter", "RankFn", "make_ranker_from_env"]
