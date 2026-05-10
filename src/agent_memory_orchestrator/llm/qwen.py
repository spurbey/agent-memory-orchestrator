from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class QwenUnavailable(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class QueryPlan:
    intent: str
    entities: list[str]
    include_raw: bool = False
    include_historical: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "entities": self.entities,
            "include_raw": self.include_raw,
            "include_historical": self.include_historical,
        }


QUERY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "decision_lookup",
                "work_history",
                "bug_fix_trace",
                "raw_evidence",
                "project_summary",
                "historical_versions",
                "general",
            ],
        },
        "entities": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "include_raw": {"type": "boolean"},
        "include_historical": {"type": "boolean"},
    },
    "required": ["intent", "entities", "include_raw", "include_historical"],
    "additionalProperties": False,
}


CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"context": {"type": "string"}},
    "required": ["context"],
    "additionalProperties": False,
}


class QwenPlanner(Protocol):
    def plan_query(self, query: str) -> QueryPlan:
        """Classify the user/agent query before GraphRAG retrieval."""

    def compress_context(self, *, query: str, nodes: list[dict[str, Any]], include_raw: bool = False) -> str:
        """Build the final context returned to Claude/Codex."""


class OllamaQwenClient:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout_seconds: float = 120.0,
        planner_timeout_seconds: float | None = None,
        compression_timeout_seconds: float | None = None,
        num_ctx: int = 2048,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.planner_timeout_seconds = planner_timeout_seconds or timeout_seconds
        self.compression_timeout_seconds = compression_timeout_seconds or timeout_seconds
        self.num_ctx = max(512, int(num_ctx))

    def plan_query(self, query: str) -> QueryPlan:
        prompt = (
            "/no_think\n"
            "Classify this AMO GraphRAG query. Return only JSON with keys: "
            "intent, entities, include_raw, include_historical. "
            "entities must be an array of short strings, not objects. "
            "include_raw is true only when the query explicitly asks for raw evidence, transcript, or logs. "
            "Do not set include_raw for implementation topics like cleaning raw artifacts. "
            "include_historical is true only when the query explicitly asks for historical, versions, superseded, or old decisions. "
            "Intent must be one of: decision_lookup, work_history, bug_fix_trace, "
            "raw_evidence, project_summary, historical_versions, general.\n"
            f"Query: {query}"
        )
        payload = self._generate_json(
            prompt,
            num_predict=180,
            timeout_seconds=self.planner_timeout_seconds,
            schema=QUERY_PLAN_SCHEMA,
        )
        intent = str(payload.get("intent") or "general")
        include_raw = bool(payload.get("include_raw"))
        if include_raw and not _is_explicit_raw_request(query):
            include_raw = False
        if intent == "raw_evidence" and not include_raw:
            intent = "general"
        return QueryPlan(
            intent=intent,
            entities=_entity_strings(payload.get("entities", [])),
            include_raw=include_raw,
            include_historical=bool(payload.get("include_historical")),
        )

    def compress_context(self, *, query: str, nodes: list[dict[str, Any]], include_raw: bool = False) -> str:
        prompt = (
            "/no_think\n"
            "Create a concise AMO memory context for an AI coding agent. "
            "Return only a JSON object with exactly one key named context. "
            "Use only these graph nodes. Cite node_id and evidence_id where relevant. "
            "Do not include raw evidence unless include_raw is true.\n"
            f"include_raw={include_raw}\nQuery: {query}\nNodes:\n"
            f"{json.dumps(nodes[:6], ensure_ascii=False, indent=2)}"
        )
        payload = self._generate_json(
            prompt,
            num_predict=700,
            timeout_seconds=self.compression_timeout_seconds,
            schema=CONTEXT_SCHEMA,
        )
        text = str(payload.get("context") or "").strip()
        if not text:
            raise QwenUnavailable("ollama returned no context text")
        return text

    def _generate_json(
        self,
        prompt: str,
        *,
        num_predict: int,
        timeout_seconds: float | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": schema or "json",
                "options": {
                    "temperature": 0,
                    "num_predict": num_predict,
                    "num_ctx": self.num_ctx,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds or self.timeout_seconds) as response:  # noqa: S310
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise QwenUnavailable(f"qwen_ollama_unavailable:{exc}") from exc
        text = str(raw.get("response") or "").strip()
        if not text:
            raise QwenUnavailable("qwen_ollama_empty_response")
        return _parse_json_object(text)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if not stripped:
        raise QwenUnavailable("qwen_ollama_empty_response")
    first_error: json.JSONDecodeError | None = None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        first_error = exc
    else:
        if isinstance(parsed, dict):
            return parsed
        raise QwenUnavailable("qwen_ollama_json_must_be_object")

    for candidate in _json_object_candidates(stripped):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise QwenUnavailable(f"qwen_ollama_invalid_json:{first_error}") from first_error


def _json_object_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char != "}" or depth == 0:
            continue
        depth -= 1
        if depth == 0 and start is not None:
            candidates.append(text[start : index + 1])
            start = None
    return candidates


class DeterministicPlanner:
    """Test-only planner used when tests should not call Ollama."""

    def plan_query(self, query: str) -> QueryPlan:
        lowered = query.lower()
        intent = "general"
        if "why" in lowered or "decide" in lowered or "decision" in lowered:
            intent = "decision_lookup"
        if "history" in lowered or "versions" in lowered:
            intent = "historical_versions"
        include_raw = "raw" in lowered or "evidence" in lowered
        entities = [term for term in _terms(query) if len(term) > 2][:8]
        return QueryPlan(intent=intent, entities=entities, include_raw=include_raw, include_historical="historical" in lowered)

    def compress_context(self, *, query: str, nodes: list[dict[str, Any]], include_raw: bool = False) -> str:
        lines = [
            "AMO GraphRAG context.",
            "Use only if relevant. Cite node_id/evidence_id when relying on it.",
            f"Query: {query}",
        ]
        for index, node in enumerate(nodes[:8], start=1):
            evidence = node.get("evidence_id") or ""
            evidence_text = f" evidence_id={evidence}" if evidence else ""
            lines.append(
                f"{index}. [{node.get('kind')}] node_id={node.get('id')} status={node.get('status')}"
                f"{evidence_text}\n   {node.get('summary') or node.get('label')}"
            )
        return "\n\n".join(lines)


def _terms(text: str) -> list[str]:
    return ["".join(ch for ch in token.lower() if ch.isalnum() or ch in {"_", "-", "."}) for token in text.split()]


def _entity_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        iterable: list[Any] = list(value.values())
    elif isinstance(value, list):
        iterable = value
    else:
        iterable = []

    entities: list[str] = []
    for item in iterable:
        if isinstance(item, dict):
            text = str(item.get("name") or item.get("label") or item.get("value") or "").strip()
        else:
            text = str(item).strip()
        if text:
            entities.append(text)
    return entities[:12]


def _is_explicit_raw_request(query: str) -> bool:
    lowered = " ".join(str(query or "").lower().split())
    raw_phrases = (
        "include raw",
        "show raw",
        "raw evidence",
        "raw payload",
        "raw transcript",
        "raw log",
        "raw logs",
        "raw jsonl",
        "raw record",
        "raw records",
        "raw event",
        "raw events",
        "evidence payload",
        "evidence ref",
        "evidence refs",
        "evidence record",
        "evidence records",
        "original payload",
        "verbatim evidence",
    )
    return any(phrase in lowered for phrase in raw_phrases)
