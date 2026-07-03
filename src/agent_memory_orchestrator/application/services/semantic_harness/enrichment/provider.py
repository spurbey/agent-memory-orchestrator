from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_memory_orchestrator.infrastructure.llm.qwen import _parse_json_object


class ExternalProviderUnavailable(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class ExternalProviderConfig:
    api_key: str
    model: str
    model_env_used: str
    api_key_env: str = "llm_api_key"
    primary_model_env: str = "mdoel2"
    fallback_model_envs: tuple[str, ...] = ("model1", "model2")
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        api_key_env: str = "llm_api_key",
        primary_model_env: str = "mdoel2",
        fallback_model_envs: tuple[str, ...] = ("model1", "model2"),
        base_url: str = "https://openrouter.ai/api/v1/chat/completions",
        timeout_seconds: float = 60.0,
    ) -> "ExternalProviderConfig":
        env = environ or os.environ
        api_key = str(env.get(api_key_env) or "").strip()
        if not api_key:
            raise ExternalProviderUnavailable("external_provider_missing_api_key")
        for model_env in (primary_model_env, *fallback_model_envs):
            model = str(env.get(model_env) or "").strip()
            if model:
                return cls(
                    api_key=api_key,
                    model=model,
                    model_env_used=model_env,
                    api_key_env=api_key_env,
                    primary_model_env=primary_model_env,
                    fallback_model_envs=tuple(fallback_model_envs),
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
        raise ExternalProviderUnavailable("external_provider_missing_model")

    @property
    def chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def as_diagnostic_dict(self) -> dict[str, object]:
        return {
            "api_key_env": self.api_key_env,
            "api_key_present": bool(self.api_key),
            "model": self.model,
            "model_env_used": self.model_env_used,
            "primary_model_env": self.primary_model_env,
            "fallback_model_envs": list(self.fallback_model_envs),
            "base_url": self.chat_completions_url,
            "timeout_seconds": self.timeout_seconds,
        }


class OpenAICompatibleJsonProvider:
    def __init__(self, config: ExternalProviderConfig) -> None:
        self.config = config

    def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1200,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": max(1, int(max_tokens)),
            "response_format": {"type": "json_object"},
        }
        raw = self._post_json(payload, timeout_seconds=timeout_seconds)
        content = _choice_content(raw)
        try:
            return _parse_json_object(content)
        except RuntimeError as exc:
            raise ExternalProviderUnavailable(f"external_provider_invalid_json:{exc}") from exc

    def _post_json(self, payload: dict[str, Any], *, timeout_seconds: float | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.chat_completions_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - explicit user-configured HTTPS provider.
                request,
                timeout=timeout_seconds or self.config.timeout_seconds,
            ) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ExternalProviderUnavailable(f"external_provider_unavailable:{type(exc).__name__}") from exc
        if not isinstance(parsed, dict):
            raise ExternalProviderUnavailable("external_provider_response_must_be_object")
        return parsed


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _choice_content(raw: dict[str, Any]) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ExternalProviderUnavailable("external_provider_empty_choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ExternalProviderUnavailable("external_provider_invalid_choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ExternalProviderUnavailable("external_provider_missing_message")
    content = str(message.get("content") or "").strip()
    if not content:
        raise ExternalProviderUnavailable("external_provider_empty_content")
    return content


__all__ = [
    "ExternalProviderConfig",
    "ExternalProviderUnavailable",
    "OpenAICompatibleJsonProvider",
    "load_env_file",
]
