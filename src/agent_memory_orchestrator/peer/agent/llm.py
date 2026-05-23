from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ...core.config import Settings
from ...llm.qwen import OllamaQwenClient, QwenUnavailable
from .prompts import FINAL_SYNTHESIS_SCHEMA, PEER_ANSWER_SCHEMA, ROOM_SUMMARY_SCHEMA
from .prompts import final_synthesis_prompt, peer_answer_prompt, room_summary_prompt


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
        try:
            return self._local_json(prompt, schema=FINAL_SYNTHESIS_SCHEMA, num_predict=900)
        except PeerAgentLlmUnavailable:
            if not allow_provider or not self.settings.peer_agent_allow_initiator_api_fallback:
                raise
            return self._provider_json(prompt, schema=FINAL_SYNTHESIS_SCHEMA)

    def summarize_room(self, *, room_context: dict[str, Any]) -> dict[str, Any]:
        prompt = room_summary_prompt(room_context=room_context)
        return self._local_json(prompt, schema=ROOM_SUMMARY_SCHEMA, num_predict=600)

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
