from __future__ import annotations

import pytest

from typing import Any

from agent_memory_orchestrator.qwen_client import OllamaQwenClient, QwenUnavailable, _parse_json_object


class _StaticQwenClient(OllamaQwenClient):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(endpoint="http://127.0.0.1:11434", model="qwen3:0.6b")
        self.payload = payload

    def _generate_json(
        self,
        prompt: str,
        *,
        num_predict: int,
        timeout_seconds: float | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.payload


def test_parse_json_object_accepts_clean_object() -> None:
    assert _parse_json_object('{"intent":"general","entities":[]}') == {
        "intent": "general",
        "entities": [],
    }


def test_parse_json_object_extracts_wrapped_object() -> None:
    payload = _parse_json_object(
        'extra text before ```json\n{"context":"node_id=context:s1:latest evidence_id=raw_1"}\n``` extra after'
    )

    assert payload == {"context": "node_id=context:s1:latest evidence_id=raw_1"}


def test_parse_json_object_rejects_non_object_json() -> None:
    with pytest.raises(QwenUnavailable, match="qwen_ollama_json_must_be_object"):
        _parse_json_object('["not", "an", "object"]')


def test_plan_query_does_not_treat_raw_artifact_work_as_raw_evidence_request() -> None:
    client = _StaticQwenClient(
        {
            "intent": "raw_evidence",
            "entities": ["clean raw artifacts"],
            "include_raw": True,
            "include_historical": False,
        }
    )

    plan = client.plan_query("update evidence_window.py to clean raw artifacts before graph extraction")

    assert plan.intent == "general"
    assert plan.include_raw is False


def test_plan_query_keeps_explicit_raw_evidence_request() -> None:
    client = _StaticQwenClient(
        {
            "intent": "raw_evidence",
            "entities": ["raw evidence"],
            "include_raw": True,
            "include_historical": False,
        }
    )

    plan = client.plan_query("show raw evidence for clean-window-smoke")

    assert plan.intent == "raw_evidence"
    assert plan.include_raw is True
