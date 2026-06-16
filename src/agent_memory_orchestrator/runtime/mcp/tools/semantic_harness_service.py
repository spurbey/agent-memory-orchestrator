from __future__ import annotations

from pathlib import Path
from typing import Any

from ....application.services.semantic_harness import SemanticHarnessRuntimeService
from ....domain.semantic_harness import HarnessQueryRequest
from ....infrastructure.sqlite.semantic_harness import SQLiteHarnessGraphRepository
from ....infrastructure.sqlite.semantic_harness import SQLiteProjectionCache
from .validation import bounded_limit as _bounded_limit
from .validation import require_text as _require_text


class SemanticHarnessToolMixin:
    def amo_harness_query(
        self,
        *,
        repo_id: str,
        intent: str,
        user_goal: str,
        mode: str = "",
        questions: list[str] | None = None,
        files: list[str] | None = None,
        symbols: list[str] | None = None,
        commits: list[str] | None = None,
        errors: list[str] | None = None,
        recent_tool_result: dict[str, Any] | None = None,
        max_cards: int = 5,
        max_tokens: int = 900,
        detail: str = "strict",
        session_id: str = "",
        already_seen_node_ids: list[str] | None = None,
        already_seen_relation_ids: list[str] | None = None,
        already_seen_card_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_repo_id = _require_text(repo_id, "repo_id")
        safe_intent = _require_text(intent, "intent")
        safe_user_goal = _require_text(user_goal, "user_goal")
        safe_detail = _detail(detail)
        request = HarnessQueryRequest(
            intent=safe_intent,
            user_goal=safe_user_goal,
            mode=str(mode or "").strip(),
            questions=tuple(_clean_strings(questions)),
            files=tuple(_clean_strings(files)),
            symbols=tuple(_clean_strings(symbols)),
            commits=tuple(_clean_strings(commits)),
            errors=tuple(_clean_strings(errors)),
            recent_tool_result=dict(recent_tool_result or {}),
            max_cards=_bounded_limit(max_cards, default=5, maximum=10),
            max_tokens=_bounded_limit(max_tokens, default=900, maximum=3000),
            detail=safe_detail,
            session_id=str(session_id or ""),
            already_seen_node_ids=tuple(_clean_strings(already_seen_node_ids)),
            already_seen_relation_ids=tuple(_clean_strings(already_seen_relation_ids)),
            already_seen_card_ids=tuple(_clean_strings(already_seen_card_ids)),
        )
        response = self._semantic_harness_runtime().query(safe_repo_id, request)
        payload = response.as_dict()
        return {
            "ok": True,
            "tool": "amo_harness_query",
            "repo_id": safe_repo_id,
            "status": response.status,
            "requires_bootstrap": response.status == "unavailable",
            "cards": payload["cards"],
            "next_actions": payload["next_actions"],
            "trace": payload["trace"],
            "warnings": payload["warnings"],
            "mode_result": payload["mode_result"],
            "response": payload,
        }

    def _semantic_harness_runtime(self) -> SemanticHarnessRuntimeService:
        runtime = getattr(self, "_semantic_harness_runtime_service", None)
        if runtime is not None:
            return runtime
        db_path = _semantic_harness_db_path(self.settings.home)
        graph_repo = SQLiteHarnessGraphRepository(db_path)
        projection_cache = SQLiteProjectionCache(db_path)
        runtime = SemanticHarnessRuntimeService(
            graph_repository=graph_repo,
            projection_cache=projection_cache,
        )
        self._semantic_harness_graph_repo = graph_repo
        self._semantic_harness_projection_cache = projection_cache
        self._semantic_harness_runtime_service = runtime
        return runtime

    def _close_semantic_harness_runtime(self) -> None:
        if graph_repo := getattr(self, "_semantic_harness_graph_repo", None):
            graph_repo.close()
        if projection_cache := getattr(self, "_semantic_harness_projection_cache", None):
            projection_cache.close()
        self._semantic_harness_graph_repo = None
        self._semantic_harness_projection_cache = None
        self._semantic_harness_runtime_service = None


def _semantic_harness_db_path(home: Path) -> Path:
    return (home / ".data" / "semantic_harness.sqlite").resolve()


def _clean_strings(values: list[str] | None) -> list[str]:
    return [str(value).strip() for value in values or [] if str(value).strip()]


def _detail(value: str) -> str:
    safe = str(value or "strict").strip().lower()
    if safe not in {"strict", "normal", "deep"}:
        return "strict"
    return safe


__all__ = ["SemanticHarnessToolMixin"]
