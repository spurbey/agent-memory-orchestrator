from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ...core.config import Settings
from ...infrastructure.llm import OllamaQwenClient, QwenUnavailable
from .prompts import FINAL_SYNTHESIS_SCHEMA, PEER_ANSWER_SCHEMA, ROOM_CONTINUATION_SCHEMA, ROOM_SUMMARY_SCHEMA
from .prompts import final_synthesis_prompt, peer_answer_prompt, room_continuation_prompt, room_summary_prompt


class PeerAgentLlmUnavailable(RuntimeError):
    pass


class PeerAgentLlmGateway:
    """Peer-agent LLM routing.

    Responder-side drafting only uses that peer's local Ollama. Initiator-side
    synthesis tries local Ollama first and may then use the initiator's own
    OpenAI-compatible provider key. Provider credentials are never serialized
    into peer room messages.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_peer_answer(
        self,
        *,
        query: str,
        retrieval_bundle: dict[str, Any],
        quality: dict[str, Any],
        room_context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = peer_answer_prompt(
            query=query,
            retrieval_bundle=retrieval_bundle,
            quality=quality,
            room_context=room_context,
        )
        return self._local_json(prompt, schema=PEER_ANSWER_SCHEMA, num_predict=700)

    def synthesize_final(
        self,
        *,
        query: str,
        local_result: dict[str, Any],
        peer_responses: list[dict[str, Any]],
        allow_provider: bool = True,
    ) -> dict[str, Any]:
        prompt = final_synthesis_prompt(query=query, local_result=local_result, peer_responses=peer_responses)
        if self.local_ollama_ready():
            try:
                return self._local_json(prompt, schema=FINAL_SYNTHESIS_SCHEMA, num_predict=900)
            except PeerAgentLlmUnavailable:
                pass
        if allow_provider and self.settings.peer_agent_allow_initiator_api_fallback and self.provider_configured():
            return self._provider_json(prompt, schema=FINAL_SYNTHESIS_SCHEMA)
        raise PeerAgentLlmUnavailable("peer_agent_no_synthesis_provider_available")

    def summarize_room(self, *, room_context: dict[str, Any]) -> dict[str, Any]:
        prompt = room_summary_prompt(room_context=room_context)
        return self._local_json(prompt, schema=ROOM_SUMMARY_SCHEMA, num_predict=600)

    def plan_room_continuation(
        self,
        *,
        room_context: dict[str, Any],
        peer_responses: list[dict[str, Any]],
        agent_state: dict[str, Any],
        allow_provider: bool = True,
    ) -> dict[str, Any]:
        prompt = room_continuation_prompt(
            room_context=room_context,
            peer_responses=peer_responses,
            agent_state=agent_state,
        )
        if self.local_ollama_ready():
            try:
                return self._local_json(prompt, schema=ROOM_CONTINUATION_SCHEMA, num_predict=700)
            except PeerAgentLlmUnavailable:
                pass
        if allow_provider and self.settings.peer_agent_allow_initiator_api_fallback and self.provider_configured():
            return self._provider_json(prompt, schema=ROOM_CONTINUATION_SCHEMA)
        raise PeerAgentLlmUnavailable("peer_agent_no_planner_provider_available")

    def local_ollama_ready(self, *, timeout_seconds: float = 0.75) -> bool:
        if self.settings.peer_agent_runtime != "ollama":
            return False
        endpoint = self.settings.peer_agent_endpoint or self.settings.qwen_endpoint
        model = self.settings.peer_agent_model or self.settings.qwen_model
        if not endpoint or not model:
            return False
        return _ollama_model_available(endpoint.rstrip("/"), model, timeout_seconds=timeout_seconds)

    def provider_configured(self) -> bool:
        if self.settings.peer_agent_api_provider != "openai_compatible":
            return False
        key_env = self.settings.peer_agent_api_key_env
        return bool(
            self.settings.peer_agent_api_base_url
            and self.settings.peer_agent_api_model
            and key_env
            and os.getenv(key_env, "")
        )

    def _local_json(self, prompt: str, *, schema: dict[str, Any], num_predict: int) -> dict[str, Any]:
        if self.settings.peer_agent_runtime != "ollama":
            raise PeerAgentLlmUnavailable("peer_agent_local_llm_unsupported")
        endpoint = self.settings.peer_agent_endpoint or self.settings.qwen_endpoint
        model = self.settings.peer_agent_model or self.settings.qwen_model
        if not endpoint or not model:
            raise PeerAgentLlmUnavailable("peer_agent_local_llm_not_configured")
        client = OllamaQwenClient(
            endpoint=endpoint,
            model=model,
            timeout_seconds=self.settings.peer_agent_timeout_seconds,
            num_ctx=self.settings.qwen_num_ctx,
        )
        try:
            return client.generate_json(
                prompt,
                num_predict=num_predict,
                timeout_seconds=self.settings.peer_agent_timeout_seconds,
                schema=schema,
            )
        except QwenUnavailable as exc:
            raise PeerAgentLlmUnavailable(str(exc)) from exc

    def _provider_json(self, prompt: str, *, schema: dict[str, Any]) -> dict[str, Any]:
        if self.settings.peer_agent_api_provider != "openai_compatible":
            raise PeerAgentLlmUnavailable("peer_agent_provider_not_configured")
        base_url = self.settings.peer_agent_api_base_url
        model = self.settings.peer_agent_api_model
        key_env = self.settings.peer_agent_api_key_env
        api_key = os.getenv(key_env, "") if key_env else ""
        if not base_url or not model or not key_env or not api_key:
            raise PeerAgentLlmUnavailable("peer_agent_provider_missing_config")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.peer_agent_timeout_seconds) as response:  # noqa: S310
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise PeerAgentLlmUnavailable(f"peer_agent_provider_unavailable:{type(exc).__name__}") from exc
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if not isinstance(choices, list) or not choices:
            raise PeerAgentLlmUnavailable("peer_agent_provider_empty_choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
        if not content:
            raise PeerAgentLlmUnavailable("peer_agent_provider_empty_content")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PeerAgentLlmUnavailable("peer_agent_provider_invalid_json") from exc
        if not isinstance(parsed, dict):
            raise PeerAgentLlmUnavailable("peer_agent_provider_json_must_be_object")
        return parsed


def _ollama_model_available(endpoint: str, model: str, *, timeout_seconds: float) -> bool:
    return _ollama_model_list_contains(
        f"{endpoint}/api/ps",
        model,
        timeout_seconds=timeout_seconds,
    ) or _ollama_model_list_contains(
        f"{endpoint}/api/tags",
        model,
        timeout_seconds=timeout_seconds,
    )


def _ollama_model_list_contains(url: str, model: str, *, timeout_seconds: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return False
    models = payload.get("models") if isinstance(payload, dict) else []
    if not isinstance(models, list):
        return False
    expected = _model_aliases(model)
    for item in models:
        if not isinstance(item, dict):
            continue
        names = {
            str(item.get("name") or "").strip(),
            str(item.get("model") or "").strip(),
            str(item.get("digest") or "").strip(),
        }
        if any(name in expected for name in names if name):
            return True
    return False


def _model_aliases(model: str) -> set[str]:
    text = str(model or "").strip()
    if not text:
        return set()
    aliases = {text}
    if ":" not in text:
        aliases.add(f"{text}:latest")
    else:
        aliases.add(text.split(":", 1)[0])
    return aliases
