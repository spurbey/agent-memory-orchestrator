from __future__ import annotations

import json
import urllib.request

import pytest

from agent_memory_orchestrator.application.services.semantic_harness.enrichment import ExternalProviderConfig
from agent_memory_orchestrator.application.services.semantic_harness.enrichment import ExternalProviderUnavailable
from agent_memory_orchestrator.application.services.semantic_harness.enrichment import OpenAICompatibleJsonProvider
from agent_memory_orchestrator.application.services.semantic_harness.enrichment import load_env_file


def test_provider_config_prefers_misspelled_primary_model() -> None:
    config = ExternalProviderConfig.from_env(
        {
            "llm_api_key": "secret-key",
            "model1": "fallback-one",
            "mdoel2": "primary-two",
            "model2": "fallback-two",
        }
    )

    assert config.model == "primary-two"
    assert config.model_env_used == "mdoel2"


def test_provider_config_falls_back_to_model1_then_model2() -> None:
    first = ExternalProviderConfig.from_env({"llm_api_key": "secret-key", "model1": "fallback-one"})
    second = ExternalProviderConfig.from_env({"llm_api_key": "secret-key", "model2": "fallback-two"})

    assert first.model == "fallback-one"
    assert first.model_env_used == "model1"
    assert second.model == "fallback-two"
    assert second.model_env_used == "model2"


def test_provider_diagnostics_never_include_api_key() -> None:
    config = ExternalProviderConfig.from_env({"llm_api_key": "secret-key", "mdoel2": "primary-two"})

    diagnostics = json.dumps(config.as_diagnostic_dict())

    assert "secret-key" not in diagnostics
    assert config.as_diagnostic_dict()["api_key_present"] is True


def test_provider_appends_chat_completions_path() -> None:
    config = ExternalProviderConfig(
        api_key="secret-key",
        model="model",
        model_env_used="mdoel2",
        base_url="https://example.test/api/v1",
    )

    assert config.chat_completions_url == "https://example.test/api/v1/chat/completions"


def test_provider_parses_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))  # type: ignore[union-attr]
        return _Response({"choices": [{"message": {"content": "{\"facts\": []}"}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAICompatibleJsonProvider(
        ExternalProviderConfig(
            api_key="secret-key",
            model="model",
            model_env_used="mdoel2",
            base_url="https://example.test/api/v1",
        )
    )

    result = provider.generate_json("return facts")

    assert result == {"facts": []}
    assert captured["url"] == "https://example.test/api/v1/chat/completions"
    assert "secret-key" in dict(captured["headers"]).get("Authorization", "")
    assert dict(captured["body"])["response_format"] == {"type": "json_object"}


def test_provider_rejects_malformed_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_request: urllib.request.Request, timeout: float) -> _Response:
        return _Response({"choices": [{"message": {"content": "not json"}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAICompatibleJsonProvider(
        ExternalProviderConfig(api_key="secret-key", model="model", model_env_used="mdoel2")
    )

    with pytest.raises(ExternalProviderUnavailable, match="external_provider_invalid_json"):
        provider.generate_json("return facts")


def test_load_env_file_handles_bom_and_quotes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env_file = tmp_path / ".env"
    env_file.write_text('\ufeffllm_api_key="secret-key"\nmodel1=fallback-one\n', encoding="utf-8")

    values = load_env_file(env_file)

    assert values["llm_api_key"] == "secret-key"
    assert values["model1"] == "fallback-one"


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")
