from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


ALLOWED_REASONING_NODE_TYPES = frozenset({"Problem", "Decision", "Cause", "Fix", "Constraint", "OpenQuestion"})
ALLOWED_REASONING_STATUSES = frozenset({"accepted", "needs_review"})

UNRESOLVED_WORDS = (
    "unknown",
    "unresolved",
    "open",
    "unclear",
    "needs decision",
    "not decided",
    "pending",
)
GENERIC_FIX_PHRASES = (
    "filter decisions based on their quality",
    "quality check was added",
    "improve quality",
    "tests passed",
    "checks passed",
)
VALIDATION_WORKFLOW_WORDS = (
    "validation workflow",
    "test workflow",
    "testing workflow",
    "quality gate tests",
    "test coverage",
    "validator",
    "validation gate",
)
RAW_INTERNAL_REF_RE = re.compile(r"(?:transcript:|tool_use:call_|tool_result:call_|call_[A-Za-z0-9]{10,})")
ALIGNMENT_STOPWORDS = {
    "add",
    "adds",
    "change",
    "changed",
    "changes",
    "code",
    "commit",
    "file",
    "files",
    "fix",
    "feat",
    "graph",
    "ui",
    "web",
    "dashboard",
    "make",
    "new",
    "path",
    "src",
    "test",
    "tests",
    "the",
    "this",
    "uses",
    "with",
}


@dataclass(slots=True, frozen=True)
class ReasoningExtractionReview:
    results: tuple[dict[str, Any], ...]
    accepted_nodes: tuple[dict[str, Any], ...]
    needs_review_nodes: tuple[dict[str, Any], ...]
    rejected_nodes: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": "04_packet_reasoning_extraction_review",
            "summary": self.summary,
            "results": list(self.results),
            "accepted_nodes": list(self.accepted_nodes),
            "needs_review_nodes": list(self.needs_review_nodes),
            "rejected_nodes": list(self.rejected_nodes),
            "diagnostics": list(self.diagnostics),
        }


def review_reasoning_extraction_results(
    *,
    packets: list[dict[str, Any]],
    results: list[dict[str, Any]],
    source_name: str = "",
) -> ReasoningExtractionReview:
    """Validate packet-wise LLM reasoning extraction output.

    This is the deterministic Stage 04 promotion gate. The LLM can propose
    nodes, but this function decides which nodes may move forward.
    """

    packets_by_id = {str(packet.get("packet_id") or ""): packet for packet in packets if str(packet.get("packet_id") or "")}
    latest_results = _latest_result_by_packet(results)

    reviewed_results: list[dict[str, Any]] = []
    accepted_all: list[dict[str, Any]] = []
    needs_review_all: list[dict[str, Any]] = []
    rejected_all: list[dict[str, Any]] = []
    diagnostics_all: list[dict[str, Any]] = []

    for packet_id in sorted(packets_by_id):
        packet = packets_by_id[packet_id]
        result = latest_results.get(packet_id)
        if result is None:
            diagnostic = {"level": "error", "kind": "missing_packet_result", "packet_id": packet_id}
            diagnostics_all.append(diagnostic)
            reviewed_results.append(
                _empty_review_result(packet=packet, source_name=source_name, diagnostics=[diagnostic])
            )
            continue

        reviewed = review_reasoning_packet_result(packet=packet, result=result, source_name=source_name)
        reviewed_results.append(reviewed)
        accepted_all.extend(reviewed["accepted_nodes"])
        needs_review_all.extend(reviewed["needs_review_nodes"])
        rejected_all.extend(reviewed["rejected_nodes"])
        diagnostics_all.extend(reviewed["diagnostics"])

    packet_ids = {str(result.get("packet_id") or "") for result in reviewed_results}
    summary = {
        "packet_count": len(reviewed_results),
        "unique_packet_count": len(packet_ids),
        "missing_packets": [pid for pid in sorted(packets_by_id) if pid not in latest_results],
        "accepted_node_count": len(accepted_all),
        "needs_review_node_count": len(needs_review_all),
        "rejected_node_count": len(rejected_all),
        "accepted_node_type_counts": _node_type_counts(accepted_all),
        "needs_review_node_type_counts": _node_type_counts(needs_review_all),
        "rejected_node_type_counts": _node_type_counts(rejected_all),
        "diagnostic_level_counts": _diagnostic_counts(diagnostics_all, "level"),
        "diagnostic_kind_counts": _diagnostic_counts(diagnostics_all, "kind"),
        "packet_error_ids": sorted(
            {
                str(diagnostic.get("packet_id") or "")
                for diagnostic in diagnostics_all
                if diagnostic.get("level") == "error" and diagnostic.get("packet_id")
            }
        ),
        "packet_warning_ids": sorted(
            {
                str(diagnostic.get("packet_id") or "")
                for diagnostic in diagnostics_all
                if diagnostic.get("level") == "warning" and diagnostic.get("packet_id")
            }
        ),
        "stage_acceptance": "PASS_WITH_REVIEW_BUCKET"
        if reviewed_results and not any(d.get("level") == "error" for d in diagnostics_all)
        else "FAIL",
    }
    return ReasoningExtractionReview(
        results=tuple(reviewed_results),
        accepted_nodes=tuple(accepted_all),
        needs_review_nodes=tuple(needs_review_all),
        rejected_nodes=tuple(rejected_all),
        diagnostics=tuple(diagnostics_all),
        summary=summary,
    )


def review_reasoning_packet_result(
    *,
    packet: dict[str, Any],
    result: dict[str, Any],
    source_name: str = "",
) -> dict[str, Any]:
    packet_id = str(packet.get("packet_id") or "")
    commit_sha = _packet_commit_sha(packet)
    allowed_refs, validation_refs = collect_packet_evidence_refs(packet)
    diagnostics: list[dict[str, Any]] = []

    parsed = result.get("parsed_output")
    if parsed is None and result.get("raw_output"):
        try:
            parsed = extract_json_object(str(result.get("raw_output") or ""))
        except Exception as exc:
            diagnostics.append(
                {
                    "level": "error",
                    "kind": "json_parse_failed",
                    "packet_id": packet_id,
                    "message": str(exc),
                    "raw_output_preview": str(result.get("raw_output") or "")[:1200],
                }
            )

    if parsed is None:
        diagnostics.append({"level": "error", "kind": "parsed_output_missing", "packet_id": packet_id})
        return _empty_review_result(packet=packet, source_name=source_name, diagnostics=diagnostics)

    accepted_nodes: list[dict[str, Any]] = []
    needs_review_nodes: list[dict[str, Any]] = []
    rejected_nodes: list[dict[str, Any]] = []

    if str(parsed.get("packet_id") or "") != packet_id:
        diagnostics.append(
            {
                "level": "error",
                "kind": "packet_id_mismatch",
                "packet_id": packet_id,
                "expected": packet_id,
                "actual": parsed.get("packet_id"),
            }
        )
    if str(parsed.get("commit_sha") or "") != commit_sha:
        diagnostics.append(
            {
                "level": "error",
                "kind": "commit_sha_mismatch",
                "packet_id": packet_id,
                "expected": commit_sha,
                "actual": parsed.get("commit_sha"),
            }
        )

    nodes = parsed.get("nodes", [])
    if not isinstance(nodes, list):
        diagnostics.append({"level": "error", "kind": "nodes_not_list", "packet_id": packet_id})
        nodes = []
    if len(nodes) > 6:
        diagnostics.append({"level": "warning", "kind": "too_many_nodes", "packet_id": packet_id, "node_count": len(nodes)})

    for index, raw_node in enumerate(nodes):
        node, node_diagnostics = validate_reasoning_node(
            raw_node,
            packet_id=packet_id,
            commit_sha=commit_sha,
            index=index,
            allowed_refs=allowed_refs,
            validation_refs=validation_refs,
            source_name=source_name,
        )
        diagnostics.extend(node_diagnostics)
        action = node["post_validation"]["action"]
        if action != "reject":
            alignment = reasoning_commit_alignment(packet, node)
            node["post_validation"]["semantic_alignment"] = alignment
            if alignment["status"] == "low_overlap":
                diagnostics.append(
                    _diag(
                        "warning",
                        "semantic_alignment_low_overlap",
                        packet_id,
                        index=index,
                        commit_message=alignment["commit_message"],
                        changed_file_sample=alignment["changed_file_sample"],
                        overlap_terms=alignment["overlap_terms"],
                    )
                )
                reasons = list(node["post_validation"].get("reasons") or [])
                reasons.append("Reasoning text has low semantic overlap with commit message and changed files")
                node["post_validation"] = {**node["post_validation"], "action": "needs_review", "reasons": reasons}
                node["status"] = "needs_review"
                action = "needs_review"
        if action == "reject":
            rejected_nodes.append(node)
        elif action == "needs_review":
            needs_review_nodes.append(node)
        else:
            accepted_nodes.append(node)

    return {
        "packet_id": packet_id,
        "commit_sha": commit_sha,
        "source_name": source_name,
        "diagnostics": diagnostics,
        "accepted_nodes": accepted_nodes,
        "needs_review_nodes": needs_review_nodes,
        "rejected_nodes": rejected_nodes,
    }


def validate_reasoning_node(
    raw_node: Any,
    *,
    packet_id: str,
    commit_sha: str,
    index: int,
    allowed_refs: set[str],
    validation_refs: set[str],
    source_name: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    node = dict(raw_node) if isinstance(raw_node, dict) else {}
    node_type = str(node.get("node_type") or "")
    status = str(node.get("status") or "")
    refs = [str(ref).strip() for ref in node.get("evidence_refs", []) if str(ref).strip()] if isinstance(node.get("evidence_refs"), list) else []
    confidence = node.get("confidence")
    statement = str(node.get("statement") or "")
    reason = str(node.get("reason") or "")
    combined = f"{statement} {reason}".lower()

    diagnostics: list[dict[str, Any]] = []
    hard_error = False
    review_reasons: list[str] = []

    if node_type not in ALLOWED_REASONING_NODE_TYPES:
        diagnostics.append(_diag("error", "invalid_node_type", packet_id, index=index, node_type=node_type))
        hard_error = True
    if status not in ALLOWED_REASONING_STATUSES:
        diagnostics.append(_diag("error", "invalid_status", packet_id, index=index, status=status))
        hard_error = True
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        diagnostics.append(_diag("error", "invalid_confidence", packet_id, index=index, confidence=confidence))
        hard_error = True
    if not refs:
        diagnostics.append(_diag("error", "missing_evidence_refs", packet_id, index=index))
        hard_error = True

    bad_refs = [ref for ref in refs if ref not in allowed_refs]
    if bad_refs:
        diagnostics.append(_diag("error", "evidence_refs_not_in_packet", packet_id, index=index, bad_refs=bad_refs))
        hard_error = True
    raw_refs = [ref for ref in refs if RAW_INTERNAL_REF_RE.search(ref)]
    if raw_refs:
        diagnostics.append(_diag("error", "raw_internal_id_leak", packet_id, index=index, refs=raw_refs))
        hard_error = True

    if node_type == "OpenQuestion" and not any(word in statement.lower() for word in UNRESOLVED_WORDS):
        diagnostics.append(_diag("warning", "open_question_likely_mislabeled", packet_id, index=index, statement=statement))
        review_reasons.append("OpenQuestion does not look unresolved")

    validation_only = bool(refs) and set(refs).issubset(validation_refs)
    validation_workflow_changed = any(word in combined for word in VALIDATION_WORKFLOW_WORDS)
    if validation_only and node_type in {"Problem", "Cause", "Decision", "Fix", "Constraint"} and not validation_workflow_changed:
        diagnostics.append(_diag("warning", "validation_only_reasoning_node", packet_id, index=index, node_type=node_type, refs=refs))
        review_reasons.append("Reasoning node cites only validation refs")

    if node_type == "Fix" and any(phrase in statement.lower() for phrase in GENERIC_FIX_PHRASES):
        diagnostics.append(_diag("warning", "fix_statement_too_generic", packet_id, index=index, statement=statement))
        review_reasons.append("Fix statement is too generic")

    if (
        node_type == "Constraint"
        and ("pass pytest" in statement.lower() or "tests passed" in statement.lower() or "must pass" in statement.lower())
        and set(refs).intersection(validation_refs)
    ):
        diagnostics.append(_diag("warning", "validation_as_constraint", packet_id, index=index, statement=statement))
        review_reasons.append("Validation result represented as durable Constraint")

    action = "reject" if hard_error else "needs_review" if status == "needs_review" or review_reasons else "accept"
    normalized = {
        "node_id": stable_reasoning_node_id(packet_id=packet_id, commit_sha=commit_sha, index=index, node=node),
        "node_type": node_type,
        "subject": str(node.get("subject") or ""),
        "statement": statement,
        "reason": reason,
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0.0,
        "evidence_refs": refs,
        "status": "needs_review" if action == "needs_review" else "accepted" if action == "accept" else "rejected",
        "source_packet_id": packet_id,
        "source_commit_sha": commit_sha,
        "post_validation": {"action": action, "reasons": review_reasons},
    }
    if source_name:
        normalized["source_result_file"] = source_name
    return normalized, diagnostics


def reasoning_commit_alignment(packet: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    commit_message = str(commit.get("message") or "")
    changed_files = [
        str(path)
        for path in commit.get("changed_file_sample", [])
        if str(path).strip()
    ] if isinstance(commit.get("changed_file_sample"), list) else []
    node_text = " ".join(
        str(node.get(key) or "")
        for key in ("node_type", "subject", "statement", "reason")
    )
    anchor_terms = _alignment_terms(commit_message)
    file_terms = set().union(*(_path_alignment_terms(path) for path in changed_files)) if changed_files else set()
    evidence_terms = (anchor_terms | file_terms) - {"py", "js", "css", "html", "md"}
    node_terms = _alignment_terms(node_text)
    if not evidence_terms or not node_terms:
        return {
            "status": "unknown",
            "overlap_terms": [],
            "commit_message": commit_message,
            "changed_file_sample": changed_files,
        }
    overlap = sorted(node_terms.intersection(evidence_terms))
    status = "aligned" if overlap else "low_overlap"
    return {
        "status": status,
        "overlap_terms": overlap,
        "commit_message": commit_message,
        "changed_file_sample": changed_files,
        "anchor_terms": sorted(anchor_terms),
        "file_terms": sorted(file_terms)[:40],
    }


def _alignment_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", str(text).lower())
        if term not in ALIGNMENT_STOPWORDS and not re.fullmatch(r"[0-9a-f]{6,40}", term)
    }


def _path_alignment_terms(path: str) -> set[str]:
    return _alignment_terms(str(path).replace("/", " ").replace("\\", " ").replace("_", " ").replace("-", " ").replace(".", " "))


def collect_packet_evidence_refs(packet: dict[str, Any]) -> tuple[set[str], set[str]]:
    allowed_refs: set[str] = set()
    validation_refs: set[str] = set()
    for key in ("problem_refs", "rationale_refs", "validation_refs"):
        for item in packet.get(key, []) if isinstance(packet.get(key), list) else []:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref") or "").strip()
            if not ref:
                continue
            allowed_refs.add(ref)
            if key == "validation_refs":
                validation_refs.add(ref)
    return allowed_refs, validation_refs


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON output is not an object")
    return parsed


def stable_reasoning_node_id(*, packet_id: str, commit_sha: str, index: int, node: dict[str, Any]) -> str:
    payload = "|".join(
        [
            packet_id,
            commit_sha,
            str(node.get("node_type") or ""),
            str(node.get("subject") or ""),
            str(node.get("statement") or ""),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"reason:{packet_id}:{commit_sha}:{index:02d}:{digest}"


def _latest_result_by_packet(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        packet_id = str(result.get("packet_id") or "")
        if packet_id:
            latest[packet_id] = result
    return latest


def _empty_review_result(
    *,
    packet: dict[str, Any],
    source_name: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "packet_id": str(packet.get("packet_id") or ""),
        "commit_sha": _packet_commit_sha(packet),
        "source_name": source_name,
        "diagnostics": diagnostics,
        "accepted_nodes": [],
        "needs_review_nodes": [],
        "rejected_nodes": [],
    }


def _packet_commit_sha(packet: dict[str, Any]) -> str:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    return str(commit.get("short_sha") or packet.get("commit_sha") or "")


def _diag(level: str, kind: str, packet_id: str, **extra: Any) -> dict[str, Any]:
    return {"level": level, "kind": kind, "packet_id": packet_id, **extra}


def _node_type_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        node_type = str(node.get("node_type") or "")
        if node_type:
            counts[node_type] = counts.get(node_type, 0) + 1
    return counts


def _diagnostic_counts(diagnostics: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        value = str(diagnostic.get(key) or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts
