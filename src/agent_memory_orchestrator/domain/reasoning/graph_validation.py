from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from .models import ANSWER_GRADE_KINDS
from .models import VALID_EXTRACTION_RUN_STATUSES
from .models import VALID_GRAPH_STATUSES
from .models import ExtractionRun


QWEN_CONFIDENCE_THRESHOLD = 0.75

VALID_EDGE_KINDS = frozenset(
    {
        "CAUSED_BY",
        "COMMITTED_AS",
        "CONFLICTS_WITH",
        "CREATED_CODE_NODE",
        "CREATED_WORK_CHANGE",
        "DUPLICATE_OF",
        "FAILED_VALIDATION",
        "FOLLOWED_BY",
        "INVALIDATES",
        "LINKED_TO_COMMIT",
        "MEMBER_OF",
        "MODIFIES",
        "PRODUCED_CHANGE_IN",
        "REFINES",
        "REVERTS",
        "SUPERSEDES",
        "SUPERSEDED_BY",
        "VALIDATED_BY",
    }
)

STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"session_final", "abandoned"},
    "session_final": {"active", "committed", "abandoned"},
    "active": {"refined", "superseded", "contested", "abandoned"},
    "committed": {"refined", "superseded", "contested", "abandoned"},
    "refined": {"superseded", "abandoned"},
    "superseded": {"contested", "abandoned"},
    "contested": {"abandoned"},
    "contested_pending_review": {"contested", "active", "abandoned"},
    "abandoned": set(),
}


@dataclass(slots=True, frozen=True)
class ValidationIssue:
    code: str
    message: str
    field: str = ""
    severity: str = "error"
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "severity": self.severity,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class ValidationReport:
    ok: bool
    issues: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @classmethod
    def from_issues(cls, issues: list[ValidationIssue]) -> "ValidationReport":
        errors = tuple(issue for issue in issues if issue.severity == "error")
        warnings = tuple(issue for issue in issues if issue.severity != "error")
        return cls(ok=not errors, issues=errors, warnings=warnings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.as_dict() for issue in self.issues],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }


def validate_graph_object(obj: Any, *, qwen_threshold: float = QWEN_CONFIDENCE_THRESHOLD) -> ValidationReport:
    """Validate one typed graph object before it is eligible for Kuzu writes."""

    issues: list[ValidationIssue] = []
    data = _object_data(obj)
    kind = str(data.get("kind") or obj.__class__.__name__)
    status = str(data.get("status") or "")

    _require(data, "id", issues)
    _require(data, "session_id", issues)

    if isinstance(obj, ExtractionRun):
        _validate_extraction_run(obj, issues)
        return ValidationReport.from_issues(issues)

    if status and status not in VALID_GRAPH_STATUSES:
        issues.append(ValidationIssue("invalid_status", f"Invalid graph status: {status}", "status"))

    extraction_run_id = str(data.get("extraction_run_id") or "").strip()
    evidence_ids = tuple(str(item).strip() for item in data.get("evidence_ids", ()) if str(item).strip())

    if kind in ANSWER_GRADE_KINDS:
        if not evidence_ids:
            issues.append(
                ValidationIssue(
                    "answer_grade_missing_evidence",
                    f"{kind} requires at least one evidence id before Kuzu write",
                    "evidence_ids",
                )
            )
        if not extraction_run_id:
            issues.append(
                ValidationIssue(
                    "answer_grade_missing_extraction_run",
                    f"{kind} requires extraction_run_id before Kuzu write",
                    "extraction_run_id",
                )
            )

    source = str(data.get("source") or "").lower()
    qwen_call = str(data.get("qwen_call") or "").strip()
    if source == "qwen" or qwen_call:
        _validate_qwen_gate(data, qwen_threshold, issues)

    if kind == "CodeNode":
        _validate_code_node(data, issues)

    if kind == "CodeHunk":
        _validate_code_hunk(data, issues)

    return ValidationReport.from_issues(issues)


def validate_status_transition(old_status: str, new_status: str, *, explicit_evidence: bool = False) -> ValidationReport:
    old = str(old_status).strip()
    new = str(new_status).strip()
    issues: list[ValidationIssue] = []
    if old not in VALID_GRAPH_STATUSES:
        issues.append(ValidationIssue("invalid_old_status", f"Invalid old status: {old}", "old_status"))
    if new not in VALID_GRAPH_STATUSES:
        issues.append(ValidationIssue("invalid_new_status", f"Invalid new status: {new}", "new_status"))
    if issues:
        return ValidationReport.from_issues(issues)
    if old == new:
        return ValidationReport(ok=True)
    if new == "abandoned" and explicit_evidence:
        return ValidationReport(ok=True)
    allowed = STATUS_TRANSITIONS.get(old, set())
    if new not in allowed:
        issues.append(
            ValidationIssue(
                "invalid_status_transition",
                f"Invalid status transition: {old} -> {new}",
                "status",
                metadata={"old": old, "new": new},
            )
        )
    return ValidationReport.from_issues(issues)


def validate_reasoning_edge(edge: Any) -> ValidationReport:
    issues: list[ValidationIssue] = []
    data = _object_data(edge)
    _require(data, "source_id", issues)
    _require(data, "target_id", issues)
    _require(data, "kind", issues)
    kind = str(data.get("kind") or "")
    if kind and kind not in VALID_EDGE_KINDS:
        issues.append(ValidationIssue("invalid_edge_kind", f"Invalid edge kind: {kind}", "kind"))
    evidence_ids = tuple(str(item).strip() for item in data.get("evidence_ids", ()) if str(item).strip())
    if not evidence_ids:
        issues.append(ValidationIssue("edge_missing_evidence", "ReasoningEdge requires evidence_ids", "evidence_ids"))
    confidence = _float(data.get("confidence"), default=-1.0)
    if confidence < 0.0 or confidence > 1.0:
        issues.append(ValidationIssue("edge_invalid_confidence", "ReasoningEdge confidence must be between 0 and 1", "confidence"))
    return ValidationReport.from_issues(issues)


def _validate_extraction_run(run: ExtractionRun, issues: list[ValidationIssue]) -> None:
    _require(run.as_dict(), "id", issues)
    _require(run.as_dict(), "session_id", issues)
    if not run.evidence_ids:
        issues.append(ValidationIssue("extraction_run_missing_evidence", "ExtractionRun requires evidence_ids", "evidence_ids"))
    if run.status not in VALID_EXTRACTION_RUN_STATUSES:
        issues.append(ValidationIssue("invalid_extraction_run_status", f"Invalid ExtractionRun status: {run.status}", "status"))


def _validate_qwen_gate(data: dict[str, Any], threshold: float, issues: list[ValidationIssue]) -> None:
    if not str(data.get("extraction_run_id") or "").strip():
        issues.append(
            ValidationIssue(
                "qwen_missing_extraction_run",
                "Qwen-derived graph output requires extraction_run_id",
                "extraction_run_id",
            )
        )
    confidence = _float(data.get("confidence"), default=-1.0)
    if confidence < threshold:
        issues.append(
            ValidationIssue(
                "qwen_low_confidence",
                f"Qwen-derived output confidence {confidence:.3f} is below threshold {threshold:.3f}",
                "confidence",
            )
        )
    if not str(data.get("qwen_call") or "").strip():
        issues.append(ValidationIssue("qwen_missing_call_name", "Qwen-derived output requires qwen_call", "qwen_call"))


def _validate_code_node(data: dict[str, Any], issues: list[ValidationIssue]) -> None:
    if not str(data.get("file_path") or "").strip():
        issues.append(ValidationIssue("code_node_missing_file", "CodeNode requires file_path", "file_path"))
    if _int(data.get("line_start")) <= 0 or _int(data.get("line_end")) <= 0:
        issues.append(ValidationIssue("code_node_invalid_lines", "CodeNode line range must be positive", "line_start"))
    if _int(data.get("line_end")) < _int(data.get("line_start")):
        issues.append(ValidationIssue("code_node_invalid_range", "CodeNode line_end must be >= line_start", "line_end"))
    if not str(data.get("content") or "").strip():
        issues.append(ValidationIssue("code_node_missing_content", "CodeNode requires content snippet", "content"))


def _validate_code_hunk(data: dict[str, Any], issues: list[ValidationIssue]) -> None:
    if not str(data.get("file_path") or "").strip():
        issues.append(ValidationIssue("code_hunk_missing_file", "CodeHunk requires file_path", "file_path"))
    if _int(data.get("new_start")) <= 0:
        issues.append(ValidationIssue("code_hunk_invalid_new_start", "CodeHunk new_start must be positive", "new_start"))
    old_start = _int(data.get("old_start"))
    old_count = _int(data.get("old_count"))
    if old_start <= 0 and old_count > 0:
        issues.append(ValidationIssue("code_hunk_invalid_old_start", "CodeHunk old_start must be positive unless old_count is zero", "old_start"))


def _object_data(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "as_dict"):
        data = obj.as_dict()
        if isinstance(data, dict):
            return data
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    raise TypeError(f"unsupported graph object type: {type(obj)!r}")


def _require(data: dict[str, Any], field_name: str, issues: list[ValidationIssue]) -> None:
    value = data.get(field_name)
    if value is None or str(value).strip() == "":
        issues.append(ValidationIssue("missing_required_field", f"Missing required field: {field_name}", field_name))


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
