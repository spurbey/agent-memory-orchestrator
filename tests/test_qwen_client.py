from __future__ import annotations

import json

import pytest

from typing import Any

from agent_memory_orchestrator.llm.qwen import OllamaQwenClient, QwenUnavailable, _parse_json_object


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


def test_generate_json_disables_ollama_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        del timeout
        captured.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"response": '{"ok": true}'})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaQwenClient(endpoint="http://127.0.0.1:11434", model="qwen3.5:9b")

    assert client.generate_json("Return JSON.", num_predict=64) == {"ok": True}
    assert captured[0]["think"] is False


def test_generate_json_falls_back_to_chat_when_generate_returns_only_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        del timeout
        payload = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, payload))
        if request.full_url.endswith("/api/generate"):
            return _FakeResponse({"response": "", "thinking": "still thinking"})
        return _FakeResponse({"message": {"content": '{"ok": true}', "thinking": ""}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaQwenClient(endpoint="http://127.0.0.1:11434", model="qwen3.5:9b")

    assert client.generate_json("Return JSON.", num_predict=64) == {"ok": True}
    assert calls[0][0].endswith("/api/generate")
    assert calls[0][1]["think"] is False
    assert calls[1][0].endswith("/api/chat")
    assert calls[1][1]["think"] is False


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")
