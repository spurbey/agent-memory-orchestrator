from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


REASONING_WORK_PACKET_SCHEMA_VERSION = "reasoning-work-packet-v1"
DEFAULT_MAX_PROBLEM_REFS = 3
DEFAULT_MAX_RATIONALE_REFS = 6
DEFAULT_MAX_VALIDATION_REFS = 3

_VALIDATION_COMMAND_RE = re.compile(
    r"\b("
    r"pytest|unittest|ruff\s+check|mypy|pyright|npm\s+(?:test|run|pack)|"
    r"pnpm\s+(?:test|run)|yarn\s+(?:test|run)|cargo\s+test|go\s+test|"
    r"flutter\s+(?:test|analyze)|dart\s+(?:test|analyze)"
    r")\b",
    re.IGNORECASE,
)
_SUPPORT_ONLY_COMMAND_RE = re.compile(
    r"\b("
    r"git\s+(?:status|diff|show|log|rev-parse|branch)|"
    r"rg|grep|findstr|select-string|get-content|cat|ls|dir|"
    r"new-item|remove-item|copy-item|move-item|mkdir|rmdir"
    r")\b",
    re.IGNORECASE,
)
_RAW_INTERNAL_ID_RE = re.compile(r"(?:transcript:[^\s\"']+|tool_(?:use|result):call_[A-Za-z0-9]+|call_[A-Za-z0-9]{10,})")


@dataclass(slots=True, frozen=True)
class ReasoningWorkPacketBuild:
    packets: tuple[dict[str, Any], ...]
    quarantined_commits: tuple[dict[str, Any], ...]
    rejected_validation_like_support: tuple[dict[str, Any], ...]
    quality: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": "03_reasoning_work_packets",
            "schema_version": REASONING_WORK_PACKET_SCHEMA_VERSION,
            "policy": "Commit-backed reasoning packets with strict validation command gating.",
            "quality": self.quality,
            "packets": list(self.packets),
            "quarantined_commits": list(self.quarantined_commits),
            "rejected_validation_like_support": list(self.rejected_validation_like_support),
        }


def build_reasoning_work_packets_from_view(
    view: dict[str, Any],
    *,
    max_problem_refs: int = DEFAULT_MAX_PROBLEM_REFS,
    max_rationale_refs: int = DEFAULT_MAX_RATIONALE_REFS,
    max_validation_refs: int = DEFAULT_MAX_VALIDATION_REFS,
) -> ReasoningWorkPacketBuild:
    """Build strict commit-backed reasoning packets from a Stage 02 evidence view.

    The input is intentionally a compact evidence view, not raw transcript data.
    Raw transcript/tool ids can stay in the support map, but this packet builder
    exposes only short evidence refs for LLM-facing reasoning extraction.
    """

    commits = sorted(_dicts(view.get("commit_facts")), key=lambda item: _sort_key(item.get("timestamp")))
    problems = sorted(_dicts(view.get("user_problems")), key=lambda item: _sort_key(item.get("timestamp")))
    rationales = sorted(_dicts(view.get("assistant_reasoning")), key=lambda item: _sort_key(item.get("timestamp")))
    validation_facts = sorted(_dicts(view.get("validation_facts")), key=lambda item: _sort_key(item.get("timestamp")))

    strict_validations: list[dict[str, Any]] = []
    rejected_validation_like_support: list[dict[str, Any]] = []
    for fact in validation_facts:
        if is_strict_validation_fact(fact):
            strict_validations.append(fact)
        else:
            rejected_validation_like_support.append(_validation_support_record(fact))

    packets: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    previous_ts: str | None = None
    for commit in commits:
        git_truth = commit.get("git_truth") if isinstance(commit.get("git_truth"), dict) else {}
        if not _commit_is_resolved(commit, git_truth):
            quarantined.append(_quarantined_commit(commit, reason="unresolved_or_fake_commit"))
            continue

        commit_ts = str(commit.get("timestamp") or "")
        packet_id = f"WP{len(packets) + 1:04d}"
        problem_refs = _rank_evidence_refs(
            problems,
            commit=commit,
            previous_ts=previous_ts,
            commit_ts=commit_ts,
            text_key="request",
            max_refs=max_problem_refs,
        )
        rationale_refs = _rank_evidence_refs(
            rationales,
            commit=commit,
            previous_ts=previous_ts,
            commit_ts=commit_ts,
            text_key="statement",
            max_refs=max_rationale_refs,
        )
        validation_refs = _validation_refs_in_window(
            strict_validations,
            previous_ts=previous_ts,
            commit_ts=commit_ts,
            max_refs=max_validation_refs,
        )

        packet = {
            "schema_version": REASONING_WORK_PACKET_SCHEMA_VERSION,
            "packet_id": packet_id,
            "packet_type": "commit_work_packet",
            "status": "candidate_reasoning_packet",
            "time_window": {"start_exclusive": previous_ts, "end_inclusive": commit_ts},
            "commit": _commit_packet(commit, git_truth),
            "problem_refs": problem_refs,
            "rationale_refs": rationale_refs,
            "validation_refs": validation_refs,
            "support_policy": "support refs may prove provenance, but are not LLM-facing reasoning truth",
        }
        packets.append(packet)
        previous_ts = commit_ts

    quality = _quality(
        view=view,
        packets=packets,
        quarantined=quarantined,
        validation_facts=validation_facts,
        strict_validations=strict_validations,
        rejected_validation_like_support=rejected_validation_like_support,
        max_problem_refs=max_problem_refs,
        max_rationale_refs=max_rationale_refs,
        max_validation_refs=max_validation_refs,
    )
    return ReasoningWorkPacketBuild(
        packets=tuple(packets),
        quarantined_commits=tuple(quarantined),
        rejected_validation_like_support=tuple(rejected_validation_like_support),
        quality=quality,
    )


def is_strict_validation_fact(fact: dict[str, Any]) -> bool:
    command = str(fact.get("command") or "")
    if not command.strip():
        return False
    return bool(_VALIDATION_COMMAND_RE.search(command))


def packet_json_contains_raw_internal_ids(value: Any) -> bool:
    return bool(_RAW_INTERNAL_ID_RE.search(_jsonish_text(value)))


def _commit_is_resolved(commit: dict[str, Any], git_truth: dict[str, Any]) -> bool:
    commit_id = str(commit.get("commit_id") or git_truth.get("commit_id") or "").strip()
    full_sha = str(git_truth.get("full_sha") or "").strip()
    return bool(commit_id and full_sha and git_truth.get("resolved") is True)


def _commit_packet(commit: dict[str, Any], git_truth: dict[str, Any]) -> dict[str, Any]:
    name_status = _name_status(git_truth.get("name_status"))
    changed_files = _strings(git_truth.get("changed_files"))
    return {
        "ref": str(commit.get("ref") or ""),
        "short_sha": str(commit.get("commit_id") or git_truth.get("commit_id") or ""),
        "full_sha": str(git_truth.get("full_sha") or ""),
        "message": str(git_truth.get("message") or commit.get("message_from_output") or ""),
        "changed_files_count": len(changed_files),
        "changed_file_sample": changed_files[:12],
        "name_status_counts": _status_counts(name_status),
    }


def _rank_evidence_refs(
    items: list[dict[str, Any]],
    *,
    commit: dict[str, Any],
    previous_ts: str | None,
    commit_ts: str,
    text_key: str,
    max_refs: int,
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in items
        if _in_window(str(item.get("timestamp") or ""), previous_ts=previous_ts, end_ts=commit_ts)
    ]
    if not candidates:
        candidates = [item for item in items if _timestamp_le(str(item.get("timestamp") or ""), commit_ts)]
    scored = [(_evidence_score(item, commit, text_key=text_key), item) for item in candidates]
    scored.sort(key=lambda pair: (-pair[0], _sort_key(pair[1].get("timestamp")), str(pair[1].get("ref") or "")))
    return [_evidence_ref(item, text_key=text_key, score=score) for score, item in scored[:max_refs]]


def _validation_refs_in_window(
    items: list[dict[str, Any]],
    *,
    previous_ts: str | None,
    commit_ts: str,
    max_refs: int,
) -> list[dict[str, Any]]:
    selected = [
        item
        for item in items
        if _in_window(str(item.get("timestamp") or ""), previous_ts=previous_ts, end_ts=commit_ts)
    ]
    selected.sort(key=lambda item: _sort_key(item.get("timestamp")))
    return [_validation_ref(item) for item in selected[:max_refs]]


def _evidence_score(item: dict[str, Any], commit: dict[str, Any], *, text_key: str) -> int:
    text = str(item.get(text_key) or "")
    git_truth = commit.get("git_truth") if isinstance(commit.get("git_truth"), dict) else {}
    message = str(git_truth.get("message") or commit.get("message_from_output") or "")
    changed_files = _strings(git_truth.get("changed_files"))
    terms = _terms(f"{message} {' '.join(changed_files)}")
    item_terms = _terms(text)
    score = len(terms.intersection(item_terms)) * 4
    for path in changed_files:
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        if stem and stem.lower() in text.lower():
            score += 6
    if any(word in text.lower() for word in ("why", "because", "decision", "fix", "problem", "build", "implement")):
        score += 3
    return score


def _evidence_ref(item: dict[str, Any], *, text_key: str, score: int) -> dict[str, Any]:
    return {
        "ref": str(item.get("ref") or ""),
        "timestamp": str(item.get("timestamp") or ""),
        "score": score,
        "excerpt": _clip(str(item.get(text_key) or ""), 420),
    }


def _validation_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": str(item.get("ref") or ""),
        "timestamp": str(item.get("timestamp") or ""),
        "status": str(item.get("status") or ""),
        "command": _clip(str(item.get("command") or ""), 420),
        "output_preview": _clip(str(item.get("output_preview") or ""), 420),
    }


def _validation_support_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": str(item.get("ref") or ""),
        "timestamp": str(item.get("timestamp") or ""),
        "command": _clip(str(item.get("command") or ""), 420),
        "reason": "not_a_strict_validation_command",
    }


def _quarantined_commit(commit: dict[str, Any], *, reason: str) -> dict[str, Any]:
    git_truth = commit.get("git_truth") if isinstance(commit.get("git_truth"), dict) else {}
    return {
        "ref": str(commit.get("ref") or ""),
        "timestamp": str(commit.get("timestamp") or ""),
        "commit_id": str(commit.get("commit_id") or git_truth.get("commit_id") or ""),
        "message": str(git_truth.get("message") or commit.get("message_from_output") or ""),
        "reason": reason,
    }


def _quality(
    *,
    view: dict[str, Any],
    packets: list[dict[str, Any]],
    quarantined: list[dict[str, Any]],
    validation_facts: list[dict[str, Any]],
    strict_validations: list[dict[str, Any]],
    rejected_validation_like_support: list[dict[str, Any]],
    max_problem_refs: int,
    max_rationale_refs: int,
    max_validation_refs: int,
) -> dict[str, Any]:
    commit_status_counts: dict[str, int] = {}
    for packet in packets:
        message = str(packet["commit"].get("message") or "")
        prefix = message.split(":", 1)[0].strip() or "unknown"
        commit_status_counts[prefix] = commit_status_counts.get(prefix, 0) + 1
    has_raw_ids = packet_json_contains_raw_internal_ids({"packets": packets})
    return {
        "input_stage": str(view.get("stage") or ""),
        "packet_count": len(packets),
        "quarantined_commit_count": len(quarantined),
        "stage2b_validation_fact_count": len(validation_facts),
        "strict_validation_fact_count": len(strict_validations),
        "rejected_validation_like_support_count": len(rejected_validation_like_support),
        "packets_without_problem_refs": sum(1 for packet in packets if not packet["problem_refs"]),
        "packets_without_rationale_refs": sum(1 for packet in packets if not packet["rationale_refs"]),
        "packets_without_validation_refs": sum(1 for packet in packets if not packet["validation_refs"]),
        "packets_with_raw_internal_ids_in_main_json": has_raw_ids,
        "max_problem_refs_per_packet": max_problem_refs,
        "max_rationale_refs_per_packet": max_rationale_refs,
        "max_validation_refs_per_packet": max_validation_refs,
        "commit_status_counts": commit_status_counts,
        "stage_acceptance": "PASS" if packets and not has_raw_ids else "FAIL",
        "stage_acceptance_note": "Strict packet shape accepted." if packets and not has_raw_ids else "Packet output failed strict shape checks.",
    }


def _status_counts(items: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "")
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


def _name_status(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append({"status": str(item.get("status") or ""), "path": str(item.get("path") or "")})
    return out


def _in_window(timestamp: str, *, previous_ts: str | None, end_ts: str) -> bool:
    if not timestamp or not end_ts or not _timestamp_le(timestamp, end_ts):
        return False
    return previous_ts is None or _timestamp_lt(previous_ts, timestamp)


def _timestamp_le(left: str, right: str) -> bool:
    return _parse_time(left) <= _parse_time(right)


def _timestamp_lt(left: str, right: str) -> bool:
    return _parse_time(left) < _parse_time(right)


def _sort_key(value: Any) -> datetime:
    return _parse_time(str(value or ""))


def _parse_time(value: str) -> datetime:
    if not value:
        return datetime.min
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min


def _terms(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if len(token) > 2}


def _strings(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clip(text: str, limit: int) -> str:
    cleaned = _RAW_INTERNAL_ID_RE.sub("[internal-id]", " ".join(text.split()))
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 14)].rstrip() + " ... <clipped>"


def _jsonish_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_jsonish_text(item)}" for key, item in value.items())
    if isinstance(value, list | tuple):
        return " ".join(_jsonish_text(item) for item in value)
    return str(value)
