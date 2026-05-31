from __future__ import annotations

from typing import Any


def _indexed_graph_retrieval_ready(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is not True:
        return False
    retrieval = payload.get("retrieval") if isinstance(payload.get("retrieval"), dict) else {}
    hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
    answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
    return bool(hits or str(answer.get("text") or "").strip() or payload.get("central_answer_trace"))


def _indexed_unavailable_context(payload: dict[str, Any]) -> str:
    reason = str(payload.get("error") or "active_projection_missing")
    return (
        "AMO V2 central retrieval is unavailable for this repository. "
        f"Reason: {reason}. Build/apply the active retrieval projection before using repository memory."
    )


def _mcp_graph_result_from_indexed(
    payload: dict[str, Any],
    *,
    tool: str,
    query: str,
    repo_id: str,
    limit: int,
) -> dict[str, Any]:
    retrieval = payload.get("retrieval") if isinstance(payload.get("retrieval"), dict) else {}
    hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
    answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
    public_hits = [
        _mcp_agent_hit_from_retrieval_hit(idx, hit)
        for idx, hit in enumerate(hits[:limit], 1)
        if isinstance(hit, dict)
    ]
    version_history = _version_history_from_answer_and_hits(answer=answer, public_hits=public_hits)
    context = str(answer.get("text") or "").strip()
    if not context:
        context = "Use these retrieved memory hits to answer the user. Do not treat this as final prose."
    return {
        "ok": True,
        "tool": tool,
        "query": query,
        "retrieval_mode": "v2_active_repository_memory",
        "repo": {"id": repo_id},
        "context_for_synthesis": context,
        "hits": public_hits,
        "version_history": version_history,
        "retrieval_status": {
            "vector": str(retrieval.get("vector_status") or ""),
            "source": "v2_active_projection",
            "repo_id": repo_id,
        },
        "answer_trace": payload.get("central_answer_trace") or {},
    }


def _mcp_agent_hit_from_retrieval_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document") if isinstance(hit.get("document"), dict) else {}
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    doc_type = str(document.get("doc_type") or "")
    atom_kind = str(node_metadata.get("atom_kind") or "")
    kind = atom_kind or doc_type or str(document.get("node_kind") or "")
    files = _public_files(document=document, metadata=metadata, version_metadata=version_metadata)
    commit = _public_commit(document=document, metadata=metadata, version_metadata=version_metadata)
    return {
        "rank": rank,
        "kind": kind,
        "doc_type": doc_type,
        "title": str(document.get("title") or ""),
        "summary": _public_hit_summary(document=document, metadata=metadata, version_metadata=version_metadata),
        "why_it_matched": _why_hit_matched(hit=hit, document=document, files=files),
        "status": _public_status(document=document, node_metadata=node_metadata, version_metadata=version_metadata),
        "commit": commit,
        "files": files,
        "evidence": _public_evidence(metadata=metadata),
        "score": hit.get("score"),
    }


def _public_hit_summary(*, document: dict[str, Any], metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    doc_type = str(document.get("doc_type") or "")
    body = str(document.get("body") or "")
    if doc_type == "central_version":
        atom_kind = str((metadata.get("node_metadata") or {}).get("atom_kind") or "")
        if atom_kind == "file":
            file_path = _first_text(version_metadata.get("file_path"), _body_field(body, "file_path"))
            producing_commit_sha = _first_text(version_metadata.get("producing_commit_sha"), _body_field(body, "producing_commit_sha"))
            suffix = f" produced by commit {producing_commit_sha[:12]}" if producing_commit_sha else ""
            return f"Active file memory for {file_path}{suffix}." if file_path else "Active file memory."
        for key in ("statement", "summary", "rationale"):
            text = str(version_metadata.get(key) or "").strip()
            if text:
                return _compact_text(text, 900)
    evidence_summary = _summary_from_public_evidence(_public_evidence(metadata=metadata))
    if evidence_summary:
        return evidence_summary
    reasons = metadata.get("reasons")
    if isinstance(reasons, list) and reasons:
        return _compact_text(" ".join(str(reason) for reason in reasons if str(reason).strip()), 900)
    for prefix in ("FileImpactSummary:", "CodeImpactSummary:", "Packet:"):
        body = body.replace(prefix, "").strip()
    return _compact_text(body.split("\n{", 1)[0], 900)


def _public_status(*, document: dict[str, Any], node_metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    for value in (node_metadata.get("status"), version_metadata.get("status"), document.get("memory_class")):
        text = str(value or "").strip()
        if text:
            return text
    return "retrieved"


def _public_commit(
    *,
    document: dict[str, Any],
    metadata: dict[str, Any],
    version_metadata: dict[str, Any],
) -> dict[str, str]:
    commit_sha = _first_text(
        document.get("commit_sha"),
        metadata.get("commit_sha"),
        version_metadata.get("producing_commit_sha"),
        _first_list_value(version_metadata.get("linked_commits")),
        _body_field(str(document.get("body") or ""), "producing_commit_sha"),
    )
    commit = metadata.get("commit") if isinstance(metadata.get("commit"), dict) else {}
    message = _first_text(commit.get("message"), _first_list_value(metadata.get("commit_messages")))
    if not commit_sha and not message:
        return {}
    return {"sha": commit_sha[:12], "message": message}


def _public_files(
    *,
    document: dict[str, Any],
    metadata: dict[str, Any],
    version_metadata: dict[str, Any],
) -> list[str]:
    values: list[Any] = [
        version_metadata.get("linked_files"),
        metadata.get("linked_files"),
        metadata.get("selected_files"),
        metadata.get("changed_file_sample"),
        metadata.get("path"),
        metadata.get("file_path"),
        version_metadata.get("file_path"),
        _body_field(str(document.get("body") or ""), "file_path"),
    ]
    return _unique_public_values(values, limit=8)


def _public_evidence(*, metadata: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for role, key in (("user_goal", "problem_refs"), ("rationale", "rationale_refs"), ("validation", "validation_refs")):
        refs = metadata.get(key)
        if not isinstance(refs, list):
            continue
        for ref in refs[:3]:
            if not isinstance(ref, dict):
                continue
            summary = str(ref.get("excerpt") or ref.get("output_preview") or ref.get("summary") or "").strip()
            if summary:
                evidence.append({"role": role, "summary": _compact_text(summary, 500)})
    return evidence[:6]


def _summary_from_public_evidence(evidence: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for role, label in (("user_goal", "User goal"), ("rationale", "Rationale"), ("validation", "Validation")):
        summary = next((item["summary"] for item in evidence if item.get("role") == role and item.get("summary")), "")
        if summary:
            parts.append(f"{label}: {summary}")
    return _compact_text(" ".join(parts), 900)


def _why_hit_matched(*, hit: dict[str, Any], document: dict[str, Any], files: list[str]) -> str:
    doc_type = str(document.get("doc_type") or "")
    if doc_type == "central_version":
        return "Matched active central memory" + (f" for {', '.join(files[:2])}" if files else "") + "."
    if doc_type in {"file_impact", "code_impact"}:
        return "Matched curated code/file impact support" + (f" for {', '.join(files[:2])}" if files else "") + "."
    if doc_type == "packet":
        return "Matched the original work packet and captured user/agent discussion."
    reasons = hit.get("reasons") if isinstance(hit.get("reasons"), list) else []
    term_reason = next((str(reason).removeprefix("term_overlap:") for reason in reasons if str(reason).startswith("term_overlap:")), "")
    if term_reason:
        return f"Matched query terms: {term_reason.replace(',', ', ')}."
    return "Matched active repository memory for the query."


def _version_history_from_answer_and_hits(*, answer: dict[str, Any], public_hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    context = answer.get("context") if isinstance(answer.get("context"), dict) else {}
    timeline = context.get("version_timeline") if isinstance(context.get("version_timeline"), dict) else {}
    entries = timeline.get("entries") if isinstance(timeline.get("entries"), list) else []
    history: list[dict[str, str]] = []
    for entry in entries[:8]:
        if not isinstance(entry, dict):
            continue
        history.append(
            {
                "commit": str(entry.get("commit_sha") or "")[:12],
                "message": str(entry.get("message") or ""),
                "summary": _compact_text(str(entry.get("why") or ""), 500),
            }
        )
    if history:
        return history
    for hit in public_hits:
        commit = hit.get("commit") if isinstance(hit.get("commit"), dict) else {}
        sha = str(commit.get("sha") or "")
        if sha:
            history.append(
                {
                    "commit": sha,
                    "message": str(commit.get("message") or ""),
                    "summary": _compact_text(str(hit.get("summary") or ""), 500),
                }
            )
    return history[:8]


def _body_field(body: str, key: str) -> str:
    prefix = f"{key.strip().lower()}:"
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[-1].strip()
    return ""


def _first_list_value(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or "").strip()
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _compact_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 14)].rstrip() + " ... <clipped>"


def _unique_public_values(values: list[Any], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)
            return
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for value in values:
        visit(value)
        if len(out) >= limit:
            break
    return out[:limit]



