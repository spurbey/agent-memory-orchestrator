from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QWEN_BATCH_SCHEMA_VERSION = "qwen-batch-v1"
DEFAULT_QWEN_BATCH_RUNTIME = "external_batch"
DECISION_EXTRACTION_CALL = "decision_extraction_fallback"
DECISION_EXTRACTION_REQUIRED_FIELDS = (
    "decision_type",
    "subject",
    "predicate",
    "object",
    "reason",
    "confidence",
)


@dataclass(slots=True, frozen=True)
class QwenBatchJob:
    job_id: str
    call: str
    payload: dict[str, Any]
    payload_hash: str
    schema_version: str = QWEN_BATCH_SCHEMA_VERSION
    runtime: str = DEFAULT_QWEN_BATCH_RUNTIME
    model: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        call: str,
        payload: dict[str, Any],
        model: str = "",
        runtime: str = DEFAULT_QWEN_BATCH_RUNTIME,
        metadata: dict[str, Any] | None = None,
    ) -> "QwenBatchJob":
        payload_hash = stable_json_hash(payload)
        job_id = f"qwen_job:{call}:{payload_hash[:24]}"
        return cls(
            job_id=job_id,
            call=call,
            payload=payload,
            payload_hash=payload_hash,
            runtime=runtime,
            model=model,
            created_at=utc_now(),
            metadata=metadata or {},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "schema_version": self.schema_version,
            "runtime": self.runtime,
            "model": self.model,
            "call": self.call,
            "payload_hash": self.payload_hash,
            "payload": self.payload,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class QwenBatchResult:
    job_id: str
    call: str
    payload_hash: str
    output: dict[str, Any]
    schema_version: str = QWEN_BATCH_SCHEMA_VERSION
    runtime: str = DEFAULT_QWEN_BATCH_RUNTIME
    model: str = ""
    created_at: str = ""
    diagnostics: tuple[dict[str, Any], ...] = ()

    @classmethod
    def create(
        cls,
        *,
        job: QwenBatchJob,
        output: dict[str, Any],
        model: str = "",
        runtime: str = DEFAULT_QWEN_BATCH_RUNTIME,
        diagnostics: tuple[dict[str, Any], ...] = (),
    ) -> "QwenBatchResult":
        return cls(
            job_id=job.job_id,
            call=job.call,
            payload_hash=job.payload_hash,
            output=output,
            runtime=runtime,
            model=model or job.model,
            created_at=utc_now(),
            diagnostics=diagnostics,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "schema_version": self.schema_version,
            "runtime": self.runtime,
            "model": self.model,
            "call": self.call,
            "payload_hash": self.payload_hash,
            "output": self.output,
            "created_at": self.created_at,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(slots=True, frozen=True)
class QwenBatchValidation:
    ok: bool
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors)}


class BatchQwenDecisionExtractor:
    """Adapter that lets decision_extraction consume a validated batch result."""

    def __init__(self, *, job: QwenBatchJob, result: QwenBatchResult) -> None:
        validation = validate_qwen_batch_result(job, result)
        if not validation.ok:
            raise ValueError(f"invalid_qwen_batch_result:{','.join(validation.errors)}")
        self.job = job
        self.result = result

    def extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload_hash = stable_json_hash(payload)
        if payload_hash != self.job.payload_hash:
            raise ValueError("qwen_batch_payload_hash_mismatch")
        return self.result.output


def write_qwen_batch_job(job: QwenBatchJob, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_file_part(job.job_id)}.job.json"
    path.write_text(json.dumps(job.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_qwen_batch_result(result: QwenBatchResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_file_part(result.job_id)}.result.json"
    path.write_text(json.dumps(result.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_qwen_batch_job(path: Path) -> QwenBatchJob:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return QwenBatchJob(
        job_id=str(raw.get("job_id") or ""),
        schema_version=str(raw.get("schema_version") or ""),
        runtime=str(raw.get("runtime") or ""),
        model=str(raw.get("model") or ""),
        call=str(raw.get("call") or ""),
        payload_hash=str(raw.get("payload_hash") or ""),
        payload=raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
        created_at=str(raw.get("created_at") or ""),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    )


def load_qwen_batch_result(path: Path) -> QwenBatchResult:
    raw = json.loads(path.read_text(encoding="utf-8"))
    diagnostics = raw.get("diagnostics")
    return QwenBatchResult(
        job_id=str(raw.get("job_id") or ""),
        schema_version=str(raw.get("schema_version") or ""),
        runtime=str(raw.get("runtime") or ""),
        model=str(raw.get("model") or ""),
        call=str(raw.get("call") or ""),
        payload_hash=str(raw.get("payload_hash") or ""),
        output=raw.get("output") if isinstance(raw.get("output"), dict) else {},
        created_at=str(raw.get("created_at") or ""),
        diagnostics=tuple(item for item in diagnostics if isinstance(item, dict)) if isinstance(diagnostics, list) else (),
    )


def validate_qwen_batch_result(job: QwenBatchJob, result: QwenBatchResult) -> QwenBatchValidation:
    errors: list[str] = []
    if job.schema_version != QWEN_BATCH_SCHEMA_VERSION:
        errors.append("job_schema_version_mismatch")
    if result.schema_version != QWEN_BATCH_SCHEMA_VERSION:
        errors.append("result_schema_version_mismatch")
    if result.job_id != job.job_id:
        errors.append("job_id_mismatch")
    if result.call != job.call:
        errors.append("call_mismatch")
    if result.payload_hash != job.payload_hash:
        errors.append("payload_hash_mismatch")
    if not isinstance(result.output, dict) or not result.output:
        errors.append("empty_or_non_object_output")
    if job.call == DECISION_EXTRACTION_CALL:
        errors.extend(_decision_extraction_output_errors(result.output))
    return QwenBatchValidation(ok=not errors, errors=tuple(errors))


def stable_json_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_file_part(value: str) -> str:
    out = []
    for ch in value:
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "qwen_job"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_extraction_output_errors(output: dict[str, Any]) -> list[str]:
    decisions = output.get("decisions")
    if not isinstance(decisions, list):
        return ["decision_output_missing_decisions"]
    errors: list[str] = []
    for index, item in enumerate(decisions):
        if not isinstance(item, dict):
            errors.append(f"decision_{index}_not_object")
            continue
        missing = [field for field in DECISION_EXTRACTION_REQUIRED_FIELDS if field not in item]
        if missing:
            errors.append(f"decision_{index}_missing:{','.join(missing)}")
        try:
            float(item.get("confidence"))
        except (TypeError, ValueError):
            errors.append(f"decision_{index}_confidence_not_number")
        evidence = item.get("evidence_event_ids")
        if evidence is not None and not isinstance(evidence, list):
            errors.append(f"decision_{index}_evidence_event_ids_not_array")
    return errors
