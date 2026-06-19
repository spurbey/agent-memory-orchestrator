from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult

from .mutation import render_ranked_tool_hits


ELIGIBLE_TOOL_OUTPUT_TYPES = frozenset(
    {
        "local_shell_call_output",
        "function_call_output",
        "custom_tool_call_output",
        "apply_patch_call_output",
    }
)


@dataclass(slots=True, frozen=True)
class CapturedProxyToolOutput:
    item_type: str
    call_id: str
    output: str
    raw_ref: str


@dataclass(slots=True, frozen=True)
class ProxyMutationResult:
    payload: dict[str, Any]
    modified: bool
    raw_refs: tuple[str, ...]
    warnings: tuple[str, ...]


RawStore = Callable[[str, str], bool]
Ranker = Callable[[CapturedProxyToolOutput], RankToolHitsResult | None]


def mutate_ranked_tool_outputs(
    payload: dict[str, Any],
    *,
    raw_store: RawStore | None,
    ranker: Ranker,
) -> ProxyMutationResult:
    """Prepend AMO ranked search hints to eligible OpenAI Responses tool output.

    This is intentionally transport-free. A future HTTP/WS proxy can call this
    after parsing a request body/frame and before forwarding upstream.
    """

    if raw_store is None:
        return ProxyMutationResult(
            payload=payload,
            modified=False,
            raw_refs=(),
            warnings=("raw_store_missing",),
        )

    items, wrapper_key = _responses_items(payload)
    if not items:
        return ProxyMutationResult(payload=payload, modified=False, raw_refs=(), warnings=("no_response_items",))

    updates: dict[int, str] = {}
    raw_refs: list[str] = []
    warnings: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type not in ELIGIBLE_TOOL_OUTPUT_TYPES:
            continue
        output = item.get("output")
        if not isinstance(output, str) or not _looks_like_search_output(output):
            continue

        raw_ref = _raw_ref(output)
        try:
            stored = raw_store(raw_ref, output)
        except Exception:
            warnings.append("raw_store_failed")
            continue
        if not stored:
            warnings.append("raw_store_failed")
            continue

        captured = CapturedProxyToolOutput(
            item_type=item_type,
            call_id=str(item.get("call_id") or ""),
            output=output,
            raw_ref=raw_ref,
        )
        try:
            ranked = ranker(captured)
        except Exception:
            warnings.append("ranker_failed")
            continue
        if ranked is None or not ranked.ranked_hits:
            warnings.append("ranker_no_hits")
            continue
        updates[index] = render_ranked_tool_hits(ranked, raw_ref=raw_ref, raw_output=output)
        raw_refs.append(raw_ref)

    if not updates:
        return ProxyMutationResult(payload=payload, modified=False, raw_refs=tuple(raw_refs), warnings=tuple(warnings))

    updated = copy.deepcopy(payload)
    updated_items = _items_from_updated_payload(updated, wrapper_key=wrapper_key)
    if updated_items is None:
        return ProxyMutationResult(
            payload=payload,
            modified=False,
            raw_refs=tuple(raw_refs),
            warnings=(*warnings, "updated_items_missing"),
        )
    for index, replacement in updates.items():
        if index < len(updated_items) and isinstance(updated_items[index], dict):
            updated_items[index]["output"] = replacement
    return ProxyMutationResult(
        payload=updated,
        modified=True,
        raw_refs=tuple(dict.fromkeys(raw_refs)),
        warnings=tuple(warnings),
    )


def _responses_items(payload: dict[str, Any]) -> tuple[list[Any], str]:
    inner = payload.get("response") if payload.get("type") == "response.create" else payload
    wrapper_key = "response" if isinstance(inner, dict) and inner is not payload else ""
    if not isinstance(inner, dict):
        return [], wrapper_key
    if isinstance(inner.get("input"), list):
        return inner["input"], wrapper_key
    if isinstance(inner.get("messages"), list):
        return inner["messages"], wrapper_key
    return [], wrapper_key


def _items_from_updated_payload(payload: dict[str, Any], *, wrapper_key: str) -> list[Any] | None:
    inner = payload.get(wrapper_key) if wrapper_key else payload
    if not isinstance(inner, dict):
        return None
    if isinstance(inner.get("input"), list):
        return inner["input"]
    if isinstance(inner.get("messages"), list):
        return inner["messages"]
    return None


def _looks_like_search_output(text: str) -> bool:
    matches = 0
    for line in str(text or "").splitlines()[:80]:
        if re.match(r"^(?![A-Za-z]:)[^:\r\n]+:\d+:", line.strip()):
            matches += 1
            if matches >= 1:
                return True
    return False


def _raw_ref(text: str) -> str:
    digest = hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


__all__ = [
    "CapturedProxyToolOutput",
    "ProxyMutationResult",
    "mutate_ranked_tool_outputs",
]
