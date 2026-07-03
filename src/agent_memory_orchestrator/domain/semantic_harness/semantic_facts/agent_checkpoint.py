from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .models import ANCHOR_LOCAL_SCOPE
from .models import RELATIONSHIP_SCOPE
from .models import SOURCE_AGENT_SESSION
from .models import SemanticFactProposal
from .models import SemanticFactSourceRef
from .parser import SUPPORTED_SEMANTIC_FACT_TYPES


AGENT_CHECKPOINT_SCHEMA_VERSION = "amo-agent-semantic-checkpoint-v1"
SUPPORTED_CHECKPOINT_DERIVABILITY = frozenset(
    {
        "derivable_from_current_code",
        "requires_git_history",
        "requires_agent_session_history",
        "requires_human_intent",
        "mixed",
        "unknown",
    }
)
SUPPORTED_CHECKPOINT_SOURCE_SPANS = frozenset({"validated_committed", "final_summary"})
SUPPORTED_CHECKPOINT_REF_KINDS = frozenset(
    {
        "diff",
        "commit_message",
        "test_output",
        "tool_call",
        "user_instruction",
        "agent_final_reason",
        "provider_eval",
    }
)


@dataclass(slots=True, frozen=True)
class AgentCheckpointAnchor:
    path: str
    symbol: str = ""
    code_region_hint: str = ""
    line_start: int = 0
    line_end: int = 0
    anchor_confidence: float = 0.0
    ambiguity: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "symbol": self.symbol,
            "code_region_hint": self.code_region_hint,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "anchor_confidence": self.anchor_confidence,
            "ambiguity": self.ambiguity,
        }


@dataclass(slots=True, frozen=True)
class AgentCheckpointSourceRef:
    kind: str
    commit_sha: str = ""
    path: str = ""
    line_start: int = 0
    line_end: int = 0
    command: str = ""
    excerpt: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "commit_sha": self.commit_sha,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "command": self.command,
            "excerpt": self.excerpt,
        }


@dataclass(slots=True, frozen=True)
class AgentCheckpointTestRun:
    command: str
    status: str = "unknown"
    excerpt: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"command": self.command, "status": self.status, "excerpt": self.excerpt}


@dataclass(slots=True, frozen=True)
class AgentCheckpointFact:
    fact_type: str
    text: str
    anchors: tuple[AgentCheckpointAnchor, ...]
    source_refs: tuple[AgentCheckpointSourceRef, ...]
    derivability: str
    source_kind: str
    source_span: str
    confidence: float
    index: int

    @property
    def fact_scope(self) -> str:
        return RELATIONSHIP_SCOPE if self.fact_type == "relationship_reason" else ANCHOR_LOCAL_SCOPE

    def as_dict(self) -> dict[str, object]:
        return {
            "fact_type": self.fact_type,
            "text": self.text,
            "anchors": [anchor.as_dict() for anchor in self.anchors],
            "source_refs": [ref.as_dict() for ref in self.source_refs],
            "derivability": self.derivability,
            "source_kind": self.source_kind,
            "source_span": self.source_span,
            "confidence": self.confidence,
            "fact_scope": self.fact_scope,
            "index": self.index,
        }


@dataclass(slots=True, frozen=True)
class AgentCheckpointWorkWindow:
    window_id: str
    commit_sha: str
    commit_message: str
    changed_files: tuple[str, ...]
    tests_run: tuple[AgentCheckpointTestRun, ...]
    semantic_facts: tuple[AgentCheckpointFact, ...]
    rejected_approaches: tuple[dict[str, object], ...]
    open_questions: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "window_id": self.window_id,
            "commit_sha": self.commit_sha,
            "commit_message": self.commit_message,
            "changed_files": list(self.changed_files),
            "tests_run": [test.as_dict() for test in self.tests_run],
            "semantic_facts": [fact.as_dict() for fact in self.semantic_facts],
            "rejected_approaches": list(self.rejected_approaches),
            "open_questions": list(self.open_questions),
        }


@dataclass(slots=True, frozen=True)
class AgentSemanticCheckpoint:
    schema_version: str
    checkpoint_id: str
    parent_session_id: str
    repo_root: str
    base_commit: str
    head_commit: str
    checkpoint_time: str
    session_goal: str
    work_windows: tuple[AgentCheckpointWorkWindow, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "parent_session_id": self.parent_session_id,
            "repo_root": self.repo_root,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "checkpoint_time": self.checkpoint_time,
            "session_goal": self.session_goal,
            "work_windows": [window.as_dict() for window in self.work_windows],
        }


@dataclass(slots=True, frozen=True)
class AgentCheckpointParseResult:
    checkpoint: AgentSemanticCheckpoint | None
    diagnostics: tuple[dict[str, str], ...]

    @property
    def passed(self) -> bool:
        return self.checkpoint is not None and not any(item.get("level") == "error" for item in self.diagnostics)

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checkpoint": self.checkpoint.as_dict() if self.checkpoint else None,
            "diagnostics": list(self.diagnostics),
        }


def parse_agent_semantic_checkpoint(value: str | dict[str, Any]) -> AgentCheckpointParseResult:
    payload, diagnostics = _parse_payload(value)
    if payload is None:
        return AgentCheckpointParseResult(checkpoint=None, diagnostics=tuple(diagnostics))
    checkpoint = _checkpoint(payload, diagnostics)
    if any(item.get("level") == "error" for item in diagnostics):
        return AgentCheckpointParseResult(checkpoint=None, diagnostics=tuple(diagnostics))
    return AgentCheckpointParseResult(checkpoint=checkpoint, diagnostics=tuple(diagnostics))


def checkpoint_fact_to_semantic_fact_proposal(
    *,
    checkpoint: AgentSemanticCheckpoint,
    window: AgentCheckpointWorkWindow,
    fact: AgentCheckpointFact,
    anchor_node_ids: tuple[str, ...],
) -> SemanticFactProposal:
    source_refs = tuple(
        _semantic_source_ref(
            checkpoint=checkpoint,
            window=window,
            fact_index=fact.index,
            ref_index=ref_index,
            ref=ref,
        )
        for ref_index, ref in enumerate(fact.source_refs)
    )
    return SemanticFactProposal(
        fact_type=fact.fact_type,
        text=fact.text,
        anchor_node_ids=tuple(dict.fromkeys(anchor_node_ids)),
        source_refs=source_refs,
        derivability=fact.derivability,
        source_kind=fact.source_kind,
        fact_scope=fact.fact_scope,
        source_span=fact.source_span,
        confidence=fact.confidence,
        discovery_cost="unknown",
        as_of_commit=window.commit_sha or checkpoint.head_commit,
        verified_against_commit=checkpoint.head_commit,
        verification_status="verified_at_commit" if checkpoint.head_commit else "unverified",
        proposal_id=_proposal_id(checkpoint.checkpoint_id, window.window_id, fact.index),
    )


def _parse_payload(value: str | dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    if isinstance(value, dict):
        return value, []
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        return None, [_diag("error", "invalid_json", value=str(exc))]
    if not isinstance(parsed, dict):
        return None, [_diag("error", "checkpoint_not_object")]
    return parsed, []


def _checkpoint(raw: dict[str, Any], diagnostics: list[dict[str, str]]) -> AgentSemanticCheckpoint:
    schema_version = _required_text(raw, "schema_version", diagnostics)
    if schema_version and schema_version != AGENT_CHECKPOINT_SCHEMA_VERSION:
        diagnostics.append(_diag("error", "unsupported_schema_version", value=schema_version))
    raw_windows = raw.get("work_windows")
    if not isinstance(raw_windows, list):
        diagnostics.append(_diag("error", "missing_work_windows"))
        raw_windows = []
    windows = tuple(_work_window(item, index=index, diagnostics=diagnostics) for index, item in enumerate(raw_windows))
    return AgentSemanticCheckpoint(
        schema_version=schema_version,
        checkpoint_id=_required_text(raw, "checkpoint_id", diagnostics),
        parent_session_id=str(raw.get("parent_session_id") or ""),
        repo_root=str(raw.get("repo_root") or ""),
        base_commit=str(raw.get("base_commit") or ""),
        head_commit=_required_text(raw, "head_commit", diagnostics),
        checkpoint_time=str(raw.get("checkpoint_time") or ""),
        session_goal=str(raw.get("session_goal") or ""),
        work_windows=windows,
    )


def _work_window(raw: Any, *, index: int, diagnostics: list[dict[str, str]]) -> AgentCheckpointWorkWindow:
    if not isinstance(raw, dict):
        diagnostics.append(_diag("error", "work_window_not_object", index=index))
        raw = {}
    facts = tuple(
        fact
        for fact_index, item in enumerate(_list(raw.get("semantic_facts")))
        if (fact := _fact(item, index=fact_index, window_index=index, diagnostics=diagnostics)) is not None
    )
    return AgentCheckpointWorkWindow(
        window_id=str(raw.get("window_id") or f"window-{index}"),
        commit_sha=str(raw.get("commit_sha") or ""),
        commit_message=str(raw.get("commit_message") or ""),
        changed_files=_tuple_of_strings(raw.get("changed_files")),
        tests_run=tuple(_test_run(item) for item in _list(raw.get("tests_run"))),
        semantic_facts=facts,
        rejected_approaches=tuple(item for item in _list(raw.get("rejected_approaches")) if isinstance(item, dict)),
        open_questions=_tuple_of_strings(raw.get("open_questions")),
    )


def _fact(
    raw: Any,
    *,
    index: int,
    window_index: int,
    diagnostics: list[dict[str, str]],
) -> AgentCheckpointFact | None:
    if not isinstance(raw, dict):
        diagnostics.append(_diag("error", "semantic_fact_not_object", index=index, window_index=window_index))
        return None
    local_errors = 0
    fact_type = str(raw.get("fact_type") or "").strip()
    if fact_type not in SUPPORTED_SEMANTIC_FACT_TYPES:
        diagnostics.append(_diag("error", "unsupported_fact_type", index=index, window_index=window_index, value=fact_type))
        local_errors += 1
    text = str(raw.get("text") or "").strip()
    if not text:
        diagnostics.append(_diag("error", "missing_text", index=index, window_index=window_index))
        local_errors += 1
    anchors = tuple(
        anchor
        for anchor_index, item in enumerate(_list(raw.get("anchors")))
        if (anchor := _anchor(item, index=index, anchor_index=anchor_index, diagnostics=diagnostics)) is not None
    )
    if not anchors:
        diagnostics.append(_diag("error", "missing_anchors", index=index, window_index=window_index))
        local_errors += 1
    refs = tuple(
        ref
        for ref_index, item in enumerate(_list(raw.get("source_refs")))
        if (ref := _source_ref(item, index=index, ref_index=ref_index, diagnostics=diagnostics)) is not None
    )
    if not refs:
        diagnostics.append(_diag("error", "missing_source_refs", index=index, window_index=window_index))
        local_errors += 1
    derivability = str(raw.get("derivability") or "").strip()
    if derivability not in SUPPORTED_CHECKPOINT_DERIVABILITY:
        diagnostics.append(_diag("error", "unsupported_derivability", index=index, window_index=window_index, value=derivability))
        local_errors += 1
    source_kind = str(raw.get("source_kind") or "").strip()
    if source_kind != SOURCE_AGENT_SESSION:
        diagnostics.append(_diag("error", "unsupported_source_kind", index=index, window_index=window_index, value=source_kind))
        local_errors += 1
    source_span = str(raw.get("source_span") or "").strip()
    if source_span not in SUPPORTED_CHECKPOINT_SOURCE_SPANS:
        diagnostics.append(_diag("error", "unsupported_source_span", index=index, window_index=window_index, value=source_span))
        local_errors += 1
    if local_errors:
        return None
    return AgentCheckpointFact(
        fact_type=fact_type,
        text=text,
        anchors=anchors,
        source_refs=refs,
        derivability=derivability,
        source_kind=source_kind,
        source_span=source_span,
        confidence=_bounded_float(raw.get("confidence")),
        index=index,
    )


def _anchor(raw: Any, *, index: int, anchor_index: int, diagnostics: list[dict[str, str]]) -> AgentCheckpointAnchor | None:
    if not isinstance(raw, dict):
        diagnostics.append(_diag("error", "anchor_not_object", index=index, value=str(anchor_index)))
        return None
    path = str(raw.get("path") or "").strip()
    if not path:
        diagnostics.append(_diag("error", "anchor_missing_path", index=index, value=str(anchor_index)))
        return None
    return AgentCheckpointAnchor(
        path=path,
        symbol=str(raw.get("symbol") or "").strip(),
        code_region_hint=str(raw.get("code_region_hint") or "").strip(),
        line_start=_bounded_int(raw.get("line_start")),
        line_end=_bounded_int(raw.get("line_end")),
        anchor_confidence=_bounded_float(raw.get("anchor_confidence")),
        ambiguity=str(raw.get("ambiguity") or "").strip(),
    )


def _source_ref(
    raw: Any,
    *,
    index: int,
    ref_index: int,
    diagnostics: list[dict[str, str]],
) -> AgentCheckpointSourceRef | None:
    if not isinstance(raw, dict):
        diagnostics.append(_diag("error", "source_ref_not_object", index=index, value=str(ref_index)))
        return None
    kind = str(raw.get("kind") or "").strip()
    if kind not in SUPPORTED_CHECKPOINT_REF_KINDS:
        diagnostics.append(_diag("error", "unsupported_source_ref_kind", index=index, value=kind))
        return None
    excerpt = str(raw.get("excerpt") or "").strip()
    if not excerpt:
        diagnostics.append(_diag("warning", "source_ref_missing_excerpt", index=index, value=str(ref_index)))
    return AgentCheckpointSourceRef(
        kind=kind,
        commit_sha=str(raw.get("commit_sha") or "").strip(),
        path=str(raw.get("path") or "").strip(),
        line_start=_bounded_int(raw.get("line_start")),
        line_end=_bounded_int(raw.get("line_end")),
        command=str(raw.get("command") or "").strip(),
        excerpt=excerpt,
    )


def _test_run(raw: Any) -> AgentCheckpointTestRun:
    raw = raw if isinstance(raw, dict) else {}
    status = str(raw.get("status") or "unknown").strip()
    if status not in {"passed", "failed", "unknown"}:
        status = "unknown"
    return AgentCheckpointTestRun(
        command=str(raw.get("command") or ""),
        status=status,
        excerpt=str(raw.get("excerpt") or ""),
    )


def _semantic_source_ref(
    *,
    checkpoint: AgentSemanticCheckpoint,
    window: AgentCheckpointWorkWindow,
    fact_index: int,
    ref_index: int,
    ref: AgentCheckpointSourceRef,
) -> SemanticFactSourceRef:
    ref_id = _source_ref_id(checkpoint.checkpoint_id, window.window_id, fact_index, ref_index, ref)
    line = ref.line_start or ref.line_end
    return SemanticFactSourceRef(
        ref_id=ref_id,
        ref_kind=ref.kind,
        path=ref.path,
        line=line,
        excerpt=_clip(ref.excerpt or ref.command, 240),
    )


def _source_ref_id(
    checkpoint_id: str,
    window_id: str,
    fact_index: int,
    ref_index: int,
    ref: AgentCheckpointSourceRef,
) -> str:
    stable = "|".join(
        [
            checkpoint_id,
            window_id,
            str(fact_index),
            str(ref_index),
            ref.kind,
            ref.commit_sha,
            ref.path,
            str(ref.line_start),
            str(ref.line_end),
            ref.command,
            ref.excerpt,
        ]
    )
    return f"checkpoint_ref:{_short_hash(stable, size=24)}"


def _proposal_id(checkpoint_id: str, window_id: str, fact_index: int) -> str:
    stable = "|".join([checkpoint_id, window_id, str(fact_index)])
    return f"checkpoint_fact:{_short_hash(stable, size=24)}"


def _required_text(raw: dict[str, Any], key: str, diagnostics: list[dict[str, str]]) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        diagnostics.append(_diag("error", f"missing_{key}"))
    return value


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _bounded_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(min(1.0, max(0.0, number)), 2)


def _bounded_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _short_hash(value: str, *, size: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _diag(
    level: str,
    reason: str,
    *,
    index: int | None = None,
    window_index: int | None = None,
    value: str = "",
) -> dict[str, str]:
    out = {"level": level, "reason": reason}
    if window_index is not None:
        out["window_index"] = str(window_index)
    if index is not None:
        out["index"] = str(index)
    if value:
        out["value"] = value
    return out


__all__ = [
    "AGENT_CHECKPOINT_SCHEMA_VERSION",
    "AgentCheckpointAnchor",
    "AgentCheckpointFact",
    "AgentCheckpointParseResult",
    "AgentCheckpointSourceRef",
    "AgentCheckpointTestRun",
    "AgentCheckpointWorkWindow",
    "AgentSemanticCheckpoint",
    "checkpoint_fact_to_semantic_fact_proposal",
    "parse_agent_semantic_checkpoint",
]
