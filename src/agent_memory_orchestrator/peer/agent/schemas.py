from __future__ import annotations

import hashlib
import json
from typing import Any


CONTEXT_REQUEST = "context_request"
CONTEXT_RESPONSE = "context_response"
FINAL_SYNTHESIS = "final_synthesis"
AGENT_ERROR = "agent_error"
AGENT_NEEDS_APPROVAL = "agent_needs_approval"

RESPONSE_LLM_ANSWER = "llm_answer"
RESPONSE_RETRIEVAL_BUNDLE = "retrieval_bundle"
RESPONSE_LOW_CONFIDENCE = "low_confidence"
RESPONSE_NEEDS_APPROVAL = "needs_approval"


def stable_json_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def support_from_retrieval(
    retrieval_result: dict[str, Any],
    *,
    source_peer: str,
    visibility: str = "summary_only",
    max_items: int = 8,
) -> list[dict[str, Any]]:
    answer = retrieval_result.get("answer") if isinstance(retrieval_result.get("answer"), dict) else {}
    citations = answer.get("citations") if isinstance(answer.get("citations"), list) else []
    supports: list[dict[str, Any]] = []
    for citation in citations[:max_items]:
        if not isinstance(citation, dict):
            continue
        packet_ids = _list(citation.get("packet_ids"))
        commit_shas = _list(citation.get("commit_shas"))
        evidence_ids = _list(citation.get("evidence_ids"))
        code_nodes = _list(citation.get("code_nodes"))
        code_node_ids = _list(citation.get("code_node_ids"))
        claim = _claim_for_citation(citation)
        support = {
            "source_peer": source_peer,
            "visibility": visibility,
            "local_ref": {
                "packet_id": str(citation.get("packet_id") or (packet_ids[0] if packet_ids else "")),
                "evidence_id": evidence_ids[0] if evidence_ids else "",
                "node_id": str(citation.get("graph_node_id") or ""),
            },
            "shared_ref": {
                "repo": "",
                "commit": str(citation.get("commit_sha") or (commit_shas[0] if commit_shas else "")),
                "path": _path_from_code_nodes(code_nodes),
                "symbol": _symbol_from_code_nodes(code_nodes),
                "code_node_id": code_node_ids[0] if code_node_ids else "",
            },
            "claim": claim,
            "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest() if claim else "",
        }
        supports.append(support)
    return supports


def compact_retrieval_bundle(retrieval_result: dict[str, Any], *, max_hits: int = 5) -> dict[str, Any]:
    retrieval = retrieval_result.get("retrieval") if isinstance(retrieval_result.get("retrieval"), dict) else {}
    answer = retrieval_result.get("answer") if isinstance(retrieval_result.get("answer"), dict) else {}
    hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
    return {
        "retrieval": {
            "intent": retrieval.get("intent", ""),
            "vector_status": retrieval.get("vector_status", ""),
            "reranker": retrieval.get("reranker", ""),
            "candidate_counts": retrieval.get("candidate_counts", {}),
            "hits": hits[:max_hits],
        },
        "answer": {
            "text": answer.get("text", ""),
            "citations": answer.get("citations", [])[:max_hits] if isinstance(answer.get("citations"), list) else [],
            "node_ids": answer.get("node_ids", []) if isinstance(answer.get("node_ids"), list) else [],
        },
    }


def citation_strings(supports: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for support in supports:
        if not isinstance(support, dict):
            continue
        local = support.get("local_ref") if isinstance(support.get("local_ref"), dict) else {}
        shared = support.get("shared_ref") if isinstance(support.get("shared_ref"), dict) else {}
        for prefix, value in (
            ("commit", shared.get("commit")),
            ("path", shared.get("path")),
            ("evidence", local.get("evidence_id")),
            ("packet", local.get("packet_id")),
            ("node", local.get("node_id")),
        ):
            text = str(value or "").strip()
            if text:
                out.append(f"{prefix}:{text}")
                break
    return out


def _claim_for_citation(citation: dict[str, Any]) -> str:
    trace = citation.get("trace") if isinstance(citation.get("trace"), dict) else {}
    narrative = trace.get("narrative") if isinstance(trace.get("narrative"), list) else []
    for item in narrative:
        if isinstance(item, dict):
            text = str(item.get("summary") or item.get("label") or "").strip()
            if text:
                return text
    return str(citation.get("graph_node_id") or citation.get("doc_id") or "").strip()


def _path_from_code_nodes(code_nodes: list[str]) -> str:
    for item in code_nodes:
        text = str(item)
        if "/" in text or "\\" in text:
            return text.split("::", 1)[0]
    return ""


def _symbol_from_code_nodes(code_nodes: list[str]) -> str:
    for item in code_nodes:
        text = str(item)
        if "::" in text:
            return text.rsplit("::", 1)[-1]
    return ""


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []
