from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any, Callable, ContextManager

from ...core.config import Settings
from ...graph.store import GraphEdge
from ...graph.store import GraphNode
from ...graph.store import GraphStore
from ...graph.store import KuzuGraphStore
from ...llm.qwen import OllamaQwenClient as OllamaQwenClient  # noqa: F401
from ...llm.qwen import QwenUnavailable as QwenUnavailable  # noqa: F401
from ..code_analysis import extract_code_nodes_from_commit
from ...domain.versioning.repo_identity import resolve_repo_identity
from ..retrieval import RetrievalDocument
from ..stage4_contract import stage4_contract_hash
from ..stage4_contract import stage4_output_schema
from .constants import CENTRAL_MERGE_PLANNER_VERSION
from .constants import CODE_PARSER_POLICY_VERSION
from .constants import CURATED_GRAPH_SCHEMA_VERSION
from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import PROMOTION_POLICY_VERSION
from .constants import QUALITY_EVAL_POLICY_VERSION
from .constants import REASONING_CODE_LINK_POLICY_VERSION
from .constants import REASONING_REVIEW_POLICY_VERSION
from .constants import RETRIEVAL_PROJECTION_VERSION
from .constants import SESSION_GRAPH_WRITER_VERSION
from .constants import SYMBOL_VERSION_POLICY_VERSION
from .constants import V2_STAGES
from .store import ProductionSessionJobStore


StageFn = Callable[[dict[str, Any], Path], dict[str, Any]]


@dataclass(slots=True, frozen=True)
class StageResult:
    output_path: Path
    diagnostics: dict[str, Any]


class ProductionSessionJobRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        job_store: ProductionSessionJobStore | None = None,
        graph_store_factory: Callable[[Path], GraphStore] = KuzuGraphStore,
        stage_lock_factory: Callable[[str], ContextManager[Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.job_store = job_store or ProductionSessionJobStore(settings)
        self.graph_store_factory = graph_store_factory
        self.stage_lock_factory = stage_lock_factory or (lambda _stage: nullcontext())

    def close(self) -> None:
        self.job_store.close()

    def run_next(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        owner = f"v2-runner:{uuid.uuid4().hex}"
        job = self.job_store.acquire_next(owner=owner, lease_seconds=lease_seconds)
        if job is None:
            return {"ok": True, "ran": False, "reason": "no_pending_job"}
        job_id = str(job["job_id"])
        stage = _current_stage(job)
        artifact_dir = Path(str(job["artifact_dir"]))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = self._run_stage(job, stage, artifact_dir)
        except PendingModel as exc:
            self.job_store.set_pending_model(job_id=job_id, stage=stage, reason=exc.reason, diagnostics=exc.diagnostics)
            return {"ok": True, "ran": True, "job_id": job_id, "stage": stage, "status": "pending_model", "reason": exc.reason}
        except StageFailed as exc:
            self.job_store.fail_stage(job_id=job_id, stage=stage, reason=exc.reason, diagnostics=exc.diagnostics)
            return {"ok": False, "ran": True, "job_id": job_id, "stage": stage, "status": "failed", "error": exc.reason}
        except Exception as exc:
            self.job_store.fail_stage(
                job_id=job_id,
                stage=stage,
                reason=f"{type(exc).__name__}: {exc}",
                diagnostics={"error_type": type(exc).__name__, "error": str(exc)},
            )
            return {"ok": False, "ran": True, "job_id": job_id, "stage": stage, "status": "failed", "error": str(exc)}
        self.job_store.complete_stage(
            job_id=job_id,
            stage=stage,
            output_artifact=str(result.output_path),
            output_hash=file_sha256(result.output_path),
            diagnostics=result.diagnostics,
        )
        self.job_store.release_lock(job_id=job_id)
        updated = self.job_store.get_job(job_id) or {}
        return {
            "ok": True,
            "ran": True,
            "job_id": job_id,
            "stage": stage,
            "status": updated.get("status"),
            "next_stage": updated.get("current_stage"),
            "output_artifact": str(result.output_path),
        }

    def _run_stage(self, job: dict[str, Any], stage: str, artifact_dir: Path) -> StageResult:
        stage_dir = artifact_dir / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        input_artifact = self._stage_input_artifact(job, stage, artifact_dir)
        input_hash = self._stage_input_hash(job=job, stage=stage, input_artifact=input_artifact)
        config_hash = stage_config_hash(self.settings, stage=stage)
        existing = self.job_store.stage_row(job_id=str(job["job_id"]), stage=stage)
        output = Path(str(existing.get("output_artifact") or "")) if existing else Path()
        if (
            existing
            and existing.get("status") == "complete"
            and existing.get("input_hash") == input_hash
            and existing.get("stage_config_hash") == config_hash
            and output.exists()
        ):
            return StageResult(output_path=output, diagnostics={"reused": True})
        superseded = _superseded_stage_metadata(
            existing=existing,
            input_hash=input_hash,
            config_hash=config_hash,
            output=output,
        )
        if superseded:
            self.job_store.log_event(
                job_id=str(job["job_id"]),
                event_type="stage_superseded",
                stage=stage,
                message=f"stage superseded before rerun: {stage}",
                metadata=superseded,
            )
        self.job_store.start_stage(
            job_id=str(job["job_id"]),
            stage=stage,
            input_artifact=str(input_artifact),
            input_hash=input_hash,
            stage_config_hash=config_hash,
        )
        with self.stage_lock_factory(stage):
            result = getattr(self, f"_stage_{stage}")(job, artifact_dir, stage_dir)
        if superseded:
            diagnostics = {**result.diagnostics, "superseded_previous_stage": superseded}
            return StageResult(output_path=result.output_path, diagnostics=diagnostics)
        return result

    def _stage_input_hash(self, *, job: dict[str, Any], stage: str, input_artifact: Path) -> str:
        base_hash = path_hash(input_artifact)
        if stage != "central_version_merge":
            return base_hash
        repo_id = str(job.get("repo_id") or "") or resolve_repo_identity(str(job.get("repo_path") or "")).repo_id
        active_view = self.job_store.graph_view(repo_id=repo_id, branch="main", mode="active") or {}
        payload = {
            "base_input_hash": base_hash,
            "repo_id": repo_id,
            "active_graph_view_head": str(active_view.get("graph_commit_id") or ""),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _stage_input_artifact(self, job: dict[str, Any], stage: str, artifact_dir: Path) -> Path:
        if stage == V2_STAGES[0]:
            return self.settings.evidence_dir
        previous = V2_STAGES[V2_STAGES.index(stage) - 1]
        row = self.job_store.stage_row(job_id=str(job["job_id"]), stage=previous)
        if (row is None or not row.get("output_artifact")) and stage == "retrieval_docs" and previous == "central_version_merge":
            legacy = self.job_store.stage_row(job_id=str(job["job_id"]), stage="kuzu_write")
            if legacy is not None and legacy.get("output_artifact"):
                return Path(str(legacy["output_artifact"]))
        if row is None or not row.get("output_artifact"):
            raise RuntimeError(f"missing_previous_stage:{previous}")
        return Path(str(row["output_artifact"]))

    def _stage_evidence_view(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.evidence_packets import run_evidence_view_stage

        return run_evidence_view_stage(self, job, artifact_dir, stage_dir)

    def _stage_work_packets(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.evidence_packets import run_work_packets_stage

        return run_work_packets_stage(self, job, artifact_dir, stage_dir)

    def _stage_qwen_reasoning(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.qwen_reasoning import run_qwen_reasoning_stage

        return run_qwen_reasoning_stage(self, job, artifact_dir, stage_dir)

    def _stage_reasoning_review(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.reasoning_review import run_reasoning_review_stage

        return run_reasoning_review_stage(self, job, artifact_dir, stage_dir)

    def _stage_git_hunks(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        packets = _read_json(_stage_output(artifact_dir, "work_packets"))
        repo_root = Path(str(job.get("repo_path") or ".")).resolve()
        hunks: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for packet in packets if isinstance(packets, list) else []:
            try:
                packet_hunks, _nodes = extract_code_nodes_from_commit(
                    repo_root=repo_root,
                    commit=_packet_full_sha(packet),
                    session_id=str(job["session_id"]),
                    extraction_run_id=str(job["job_id"]),
                    evidence_ids=tuple(_packet_evidence_refs(packet)),
                )
            except Exception as exc:
                errors.append({"packet_id": str(packet.get("packet_id") or ""), "error": str(exc)})
                continue
            for index, hunk in enumerate(packet_hunks, start=1):
                hunks.append(_hunk_record(packet, hunk.as_dict(), index=index))
        output = stage_dir / "code_hunks.json"
        output.write_text(json.dumps(hunks, indent=2, ensure_ascii=False), encoding="utf-8")
        (stage_dir / "git_hunk_errors.json").write_text(json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"hunk_count": len(hunks), "error_count": len(errors), "errors": errors[:20]})

    def _stage_ast_code_nodes(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        packets = _read_json(_stage_output(artifact_dir, "work_packets"))
        hunk_records = _read_json(_stage_output(artifact_dir, "git_hunks"))
        hunk_id_map = {str(item.get("original_hunk_id") or ""): str(item.get("hunk_id") or "") for item in hunk_records if isinstance(item, dict)}
        repo_root = Path(str(job.get("repo_path") or ".")).resolve()
        code_nodes: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for packet in packets if isinstance(packets, list) else []:
            try:
                _hunks, nodes = extract_code_nodes_from_commit(
                    repo_root=repo_root,
                    commit=_packet_full_sha(packet),
                    session_id=str(job["session_id"]),
                    extraction_run_id=str(job["job_id"]),
                    evidence_ids=tuple(_packet_evidence_refs(packet)),
                )
            except Exception as exc:
                errors.append({"packet_id": str(packet.get("packet_id") or ""), "error": str(exc)})
                continue
            for node in nodes:
                code_nodes.append(_code_node_record(packet, node.as_dict(), hunk_id_map=hunk_id_map))
        output = stage_dir / "code_nodes.json"
        output.write_text(json.dumps(code_nodes, indent=2, ensure_ascii=False), encoding="utf-8")
        (stage_dir / "ast_code_node_errors.json").write_text(json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"code_node_count": len(code_nodes), "error_count": len(errors), "errors": errors[:20]})

    def _stage_symbol_versions(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        del job
        code_nodes = _read_json(_stage_output(artifact_dir, "ast_code_nodes"))
        symbols, versions, edges = _symbol_versions(code_nodes if isinstance(code_nodes, list) else [])
        output = stage_dir / "symbol_versions.json"
        output.write_text(json.dumps({"symbols": symbols, "code_versions": versions, "edges": edges}, indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"symbol_count": len(symbols), "code_version_count": len(versions), "edge_count": len(edges)})

    def _stage_reasoning_code_links(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        packets = _read_json(_stage_output(artifact_dir, "work_packets"))
        reasoning_nodes = _read_json(_stage_output(artifact_dir, "reasoning_review"))
        code_hunks = _read_json(_stage_output(artifact_dir, "git_hunks"))
        code_nodes = _read_json(_stage_output(artifact_dir, "ast_code_nodes"))
        symbol_versions = _read_json(_stage_output(artifact_dir, "symbol_versions"))
        edges = _relationship_edges(
            packets if isinstance(packets, list) else [],
            reasoning_nodes if isinstance(reasoning_nodes, list) else [],
            code_hunks if isinstance(code_hunks, list) else [],
            code_nodes if isinstance(code_nodes, list) else [],
            symbol_versions if isinstance(symbol_versions, dict) else {},
        )
        output = stage_dir / "graph_edges.json"
        output.write_text(json.dumps(edges, indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"edge_count": len(edges)})

    def _stage_kuzu_write(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.session_graph_write import run_session_graph_write_stage

        return run_session_graph_write_stage(self, job, artifact_dir, stage_dir)

    def _stage_central_version_merge(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.central_version_merge import run_central_version_merge_stage

        return run_central_version_merge_stage(self, job, artifact_dir, stage_dir)

    def _stage_retrieval_docs(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.retrieval_projection import run_retrieval_docs_stage

        return run_retrieval_docs_stage(self, job, artifact_dir, stage_dir)

    def _central_active_retrieval_docs(self, *, repo_id: str) -> tuple[list[RetrievalDocument], str]:
        from .stages.retrieval_projection import central_active_retrieval_docs

        return central_active_retrieval_docs(self, repo_id=repo_id)

    def _stage_embeddings(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.retrieval_projection import run_embeddings_stage

        return run_embeddings_stage(self, job, artifact_dir, stage_dir)

    def _stage_faiss(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.retrieval_projection import run_faiss_stage

        return run_faiss_stage(self, job, artifact_dir, stage_dir)

    def _stage_quality_eval(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.retrieval_projection import run_quality_eval_stage

        return run_quality_eval_stage(self, job, artifact_dir, stage_dir)


class PendingModel(RuntimeError):
    def __init__(self, reason: str, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


class StageFailed(RuntimeError):
    def __init__(self, reason: str, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


V2SessionJobRunner = ProductionSessionJobRunner


def require_complete_production_marker(marker: dict[str, Any] | None) -> dict[str, Any]:
    if marker is None:
        raise RuntimeError("production_v2_reset_marker_missing")
    cleaned = marker.get("cleaned") if isinstance(marker.get("cleaned"), dict) else {}
    validated = marker.get("validated") if isinstance(marker.get("validated"), dict) else {}
    if marker.get("pipeline_version") != PIPELINE_VERSION or marker.get("graph_schema_version") != GRAPH_SCHEMA_VERSION:
        raise RuntimeError("production_v2_reset_marker_version_mismatch")
    cleaned_ok = cleaned.get("graph") is True and cleaned.get("retrieval") is True
    adopted_ok = (
        marker.get("adopted_existing_v2") is True
        and validated.get("graph") is True
        and validated.get("retrieval") is True
    )
    if not cleaned_ok and not adopted_ok:
        raise RuntimeError("production_v2_reset_marker_incomplete")
    return marker


require_complete_v2_reset_marker = require_complete_production_marker


def file_sha256(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return path_hash(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def path_hash(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    if path.is_dir():
        rows: list[str] = []
        for child in sorted(path.rglob("*")):
            if child.is_file():
                rows.append(f"{child.relative_to(path)}:{hashlib.sha256(child.read_bytes()).hexdigest()}")
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return ""


def stage_config_payload(settings: Settings, *, stage: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": stage,
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
    }
    if stage == "qwen_reasoning":
        payload.update(
            {
                "qwen_model": settings.qwen_model,
                "qwen_runtime": settings.qwen_runtime,
                "qwen_num_ctx": settings.qwen_num_ctx,
                "qwen_prompt_contract_hash": stage4_contract_hash(),
                "stage4_contract_hash": stage4_contract_hash(),
                "stage4_schema_hash": hashlib.sha256(json.dumps(stage4_output_schema(), sort_keys=True).encode("utf-8")).hexdigest(),
            }
        )
    elif stage == "reasoning_review":
        payload.update(
            {
                "reasoning_review_policy_version": REASONING_REVIEW_POLICY_VERSION,
                "stage4_contract_hash": stage4_contract_hash(),
            }
        )
    elif stage == "ast_code_nodes":
        payload["code_parser_policy_version"] = CODE_PARSER_POLICY_VERSION
    elif stage == "symbol_versions":
        payload.update(
            {
                "symbol_version_policy_version": SYMBOL_VERSION_POLICY_VERSION,
                "code_parser_policy_version": CODE_PARSER_POLICY_VERSION,
            }
        )
    elif stage == "reasoning_code_links":
        payload["reasoning_code_link_policy_version"] = REASONING_CODE_LINK_POLICY_VERSION
    elif stage == "kuzu_write":
        payload.update(
            {
                "promotion_policy_version": PROMOTION_POLICY_VERSION,
                "curated_graph_schema_version": CURATED_GRAPH_SCHEMA_VERSION,
                "session_graph_writer_version": SESSION_GRAPH_WRITER_VERSION,
            }
        )
    elif stage == "central_version_merge":
        payload.update(
            {
                "central_merge_planner_version": CENTRAL_MERGE_PLANNER_VERSION,
                "curated_graph_schema_version": CURATED_GRAPH_SCHEMA_VERSION,
            }
        )
    elif stage == "retrieval_docs":
        payload.update(
            {
                "retrieval_projection_version": RETRIEVAL_PROJECTION_VERSION,
                "curated_graph_schema_version": CURATED_GRAPH_SCHEMA_VERSION,
                "retrieval_node_limit": settings.auto_retrieval_node_limit,
                "retrieval_max_doc_chars": settings.auto_retrieval_max_doc_chars,
            }
        )
    elif stage in {"embeddings", "faiss"}:
        payload.update(
            {
                "embedding_model": settings.embedding_model,
                "vector_backend": settings.vector_backend,
            }
        )
    elif stage == "quality_eval":
        payload["quality_eval_policy_version"] = QUALITY_EVAL_POLICY_VERSION
    return payload


def stage_config_hash(settings: Settings, *, stage: str) -> str:
    payload = stage_config_payload(settings, stage=stage)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _superseded_stage_metadata(
    *,
    existing: dict[str, Any] | None,
    input_hash: str,
    config_hash: str,
    output: Path,
) -> dict[str, Any]:
    if not existing or existing.get("status") != "complete":
        return {}
    old_input_hash = str(existing.get("input_hash") or "")
    old_config_hash = str(existing.get("stage_config_hash") or "")
    old_output = str(existing.get("output_artifact") or "")
    reasons: list[str] = []
    if old_input_hash != input_hash:
        reasons.append("input_hash_changed")
    if old_config_hash != config_hash:
        reasons.append("policy_version_changed")
    if not old_output or not output.exists():
        reasons.append("output_artifact_missing")
    if not reasons:
        return {}
    return {
        "validity": "superseded",
        "reason": reasons[0],
        "reasons": reasons,
        "old_input_hash": old_input_hash,
        "new_input_hash": input_hash,
        "old_stage_config_hash": old_config_hash,
        "new_stage_config_hash": config_hash,
        "old_output_artifact": old_output,
    }


def _current_stage(job: dict[str, Any]) -> str:
    stage = str(job.get("current_stage") or "")
    if stage in V2_STAGES:
        return stage
    return V2_STAGES[0]


def _stage_output(artifact_dir: Path, stage: str) -> Path:
    candidates = {
        "evidence_view": "reasoning_evidence_view.json",
        "work_packets": "reasoning_work_packets.json",
        "qwen_reasoning": "stage4_packet_reasoning_results.json",
        "reasoning_review": "accepted_reasoning_nodes.json",
        "git_hunks": "code_hunks.json",
        "ast_code_nodes": "code_nodes.json",
        "symbol_versions": "symbol_versions.json",
        "reasoning_code_links": "graph_edges.json",
        "kuzu_write": "kuzu_write_result.json",
        "central_version_merge": "merge_plan.json",
        "retrieval_docs": "retrieval_docs_result.json",
        "embeddings": "embeddings_result.json",
        "faiss": "faiss_result.json",
        "quality_eval": "quality_eval.json",
    }
    return artifact_dir / stage / candidates[stage]


def _central_merge_quality_result(artifact_dir: Path) -> Any:
    plan_result = _read_json(_stage_output(artifact_dir, "central_version_merge"))
    merge_result = artifact_dir / "central_version_merge" / "merge_result.json"
    if merge_result.exists():
        applied = _read_json(merge_result)
        if not isinstance(plan_result, dict) or not isinstance(applied, dict):
            return applied
        current_plan_id = str(plan_result.get("plan_id") or "")
        applied_plan_id = str(applied.get("plan_id") or "")
        if current_plan_id and applied_plan_id == current_plan_id:
            return applied
        return {
            **plan_result,
            "stale_merge_result": {
                "plan_id": applied_plan_id,
                "status": str(applied.get("status") or ""),
                "mode": str(applied.get("mode") or ""),
                "graph_commit_id": str((applied.get("graph_commit") if isinstance(applied.get("graph_commit"), dict) else {}).get("graph_commit_id") or ""),
            },
        }
    return plan_result


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records), encoding="utf-8")


def _session_records(evidence_dir: Path, session_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(evidence_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(record.get("session_id") or "") == session_id:
                    records.append(record)
    return records


def _first_transcript_path(records: list[dict[str, Any]]) -> str:
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        value = payload.get("transcript_path") or record.get("transcript_path")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _path_changed(left: str, right: str) -> bool:
    if not left or not right:
        return bool(left or right)
    try:
        return Path(left).resolve() != Path(right).resolve()
    except OSError:
        return left.strip().lower() != right.strip().lower()


def _should_write_artifact_kuzu(inventory: dict[str, Any]) -> bool:
    raw = os.environ.get("AMO_KUZU_ARTIFACT_WRITE_MAX_EDGES", "").strip()
    try:
        max_edges = int(raw) if raw else 25_000
    except ValueError:
        max_edges = 25_000
    if max_edges < 0:
        return True
    return int(inventory.get("manifest_edge_count") or 0) <= max_edges


def _product_manifest_info(artifact_dir: Path) -> dict[str, Any]:
    curated = artifact_dir / "kuzu_write" / "curated_graph_manifest.json"
    if not curated.exists():
        raise StageFailed(
            "curated_graph_manifest_missing",
            {
                "curated_manifest_path": str(curated),
                "compact_manifest_path": str(_compact_manifest_path(artifact_dir)),
                "input_source": "missing_curated_graph_manifest",
            },
        )
    info = _optional_product_manifest_info(artifact_dir)
    info["curated_input_hash"] = file_sha256(curated)
    return info


def _optional_product_manifest_info(artifact_dir: Path) -> dict[str, Any]:
    curated = artifact_dir / "kuzu_write" / "curated_graph_manifest.json"
    compact = _compact_manifest_path(artifact_dir)
    return {
        "curated_manifest_path": str(curated),
        "curated_manifest_exists": curated.exists(),
        "curated_input_hash": file_sha256(curated) if curated.exists() else "",
        "compact_manifest_path": str(compact),
        "compact_manifest_exists": compact.exists(),
        "trace_input_hash": file_sha256(compact) if compact.exists() else "",
    }


def _compact_manifest_path(artifact_dir: Path) -> Path:
    return artifact_dir / "kuzu_write" / "compact_graph_manifest.json"


def _promotion_summary(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": audit.get("policy", ""),
        "policy_counts": audit.get("policy_counts", {}),
        "selected_file_count": audit.get("selected_file_count", 0),
        "file_impact_count": audit.get("file_impact_count", 0),
        "selected_symbol_count": audit.get("selected_symbol_count", 0),
        "selected_code_region_count": audit.get("selected_code_region_count", 0),
    }


def _central_session_edge_write_limit() -> int:
    raw = os.environ.get("AMO_KUZU_CENTRAL_EDGE_WRITE_MAX_EDGES", "").strip()
    try:
        return int(raw) if raw else 25_000
    except ValueError:
        return 25_000


def _retrieval_documents_from_manifest(
    *,
    manifest_path: Path,
    source: str,
    job: dict[str, Any],
    repo_id: str,
    max_doc_chars: int,
    limit: int,
) -> list[RetrievalDocument]:
    manifest = _read_json(manifest_path)
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else []
    namespace = str(job["job_id"]).rsplit(":", 1)[-1][:12]
    docs: list[RetrievalDocument] = []
    for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        if limit and index >= limit:
            break
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        kind = str(node.get("kind") or "")
        if not node_id or not kind:
            continue
        metadata = _safe_json_object(str(node.get("properties_json") or "{}"))
        doc_type, memory_class, importance = _fallback_doc_profile(kind)
        metadata.update(
            {
                "repo_id": repo_id,
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "source": source,
                "job_id": job.get("job_id"),
                "session_id": job.get("session_id"),
                "original_node_id": node_id,
            }
        )
        title = str(node.get("label") or node_id)
        summary = str(node.get("summary") or "")
        body = "\n".join(
            part
            for part in (
                f"{kind}: {title}",
                summary,
                json.dumps(_retrieval_fallback_metadata(metadata), ensure_ascii=False, sort_keys=True),
            )
            if part
        )
        docs.append(
            RetrievalDocument(
                doc_id=f"retrieval:{repo_id}:{namespace}:{node_id}:1",
                doc_type=doc_type,
                graph_node_id=f"{namespace}:{node_id}",
                node_kind=kind,
                packet_id=str(node.get("packet_id") or metadata.get("packet_id") or metadata.get("source_packet_id") or ""),
                commit_sha=str(node.get("commit_sha") or metadata.get("commit_sha") or metadata.get("source_commit_sha") or ""),
                title=title,
                body=_clip_text(body, max_doc_chars),
                repo_id=repo_id,
                memory_class=memory_class,
                importance=importance,
                metadata=metadata,
            )
        )
    return docs


def _merge_cumulative_retrieval_docs(
    *,
    existing_docs: list[RetrievalDocument],
    current_docs: list[RetrievalDocument],
) -> list[RetrievalDocument]:
    """Build the active repo projection as a cumulative product-memory surface."""
    merged: dict[str, RetrievalDocument] = {}
    current_has_central_docs = any(str(doc.metadata.get("source") or "") == "central_active_graph_view" for doc in current_docs)
    for doc in existing_docs:
        source = str(doc.metadata.get("source") or "")
        if source not in {"curated_graph_manifest", "central_active_graph_view"}:
            continue
        if current_has_central_docs and source == "central_active_graph_view":
            continue
        if doc.node_kind in {"CodeNode", "CodeHunk", "Symbol", "CodeVersion"}:
            continue
        merged[doc.doc_id] = dataclass_replace(
            doc,
            metadata={key: value for key, value in doc.metadata.items() if key != "projection_id"},
        )
    for doc in current_docs:
        merged[doc.doc_id] = dataclass_replace(
            doc,
            metadata={key: value for key, value in doc.metadata.items() if key != "projection_id"},
        )
    return sorted(
        merged.values(),
        key=lambda doc: (doc.metadata.get("job_id", ""), doc.doc_type, doc.graph_node_id, doc.chunk_index, doc.doc_id),
    )


def _retrieval_projection_id(*, repo_id: str, projection_version: str, source_artifact_hash: str, doc_content_hash: str) -> str:
    payload = {
        "repo_id": repo_id,
        "projection_version": projection_version,
        "source_artifact_hash": source_artifact_hash,
        "doc_content_hash": doc_content_hash,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]
    return f"rproj:{digest}"


def _retrieval_doc_content_hash(docs: list[RetrievalDocument]) -> str:
    payload = [
        {
            "doc_id": doc.doc_id,
            "doc_type": doc.doc_type,
            "graph_node_id": doc.graph_node_id,
            "node_kind": doc.node_kind,
            "packet_id": doc.packet_id,
            "commit_sha": doc.commit_sha,
            "title": doc.title,
            "body": doc.body,
            "memory_class": doc.memory_class,
            "importance": doc.importance,
            "metadata": {key: value for key, value in doc.metadata.items() if key != "projection_id"},
        }
        for doc in docs
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _retrieval_projection_activation_gate(docs: list[RetrievalDocument]) -> dict[str, Any]:
    raw_trace_docs = [
        doc
        for doc in docs
        if doc.node_kind in {"CodeNode", "CodeHunk", "Symbol", "CodeVersion"}
        or doc.doc_type in {"session_codenode", "session_codehunk", "session_symbol", "code"}
    ]
    product_docs = [
        doc
        for doc in docs
        if doc.doc_type
        in {
            "central_atom",
            "central_version",
            "code_impact",
            "file_impact",
            "reasoning",
            "commit",
            "packet",
            "file_ref",
            "symbol_ref",
            "code_region_ref",
        }
    ]
    failures: list[str] = []
    if not docs:
        failures.append("retrieval_projection_no_docs")
    if raw_trace_docs:
        failures.append("retrieval_projection_contains_raw_trace_docs")
    if not product_docs:
        failures.append("retrieval_projection_missing_product_docs")
    return {
        "passed": not failures,
        "blocking_failures": failures,
        "summary": {
            "doc_count": len(docs),
            "product_doc_count": len(product_docs),
            "raw_trace_doc_count": len(raw_trace_docs),
            "raw_trace_examples": [
                {"doc_id": doc.doc_id, "doc_type": doc.doc_type, "node_kind": doc.node_kind, "title": doc.title}
                for doc in raw_trace_docs[:5]
            ],
        },
    }


def _clip_text(text: str, limit: int) -> str:
    safe_limit = max(256, int(limit or 0))
    if len(text) <= safe_limit:
        return text
    return text[: max(0, safe_limit - 18)].rstrip() + "\n... <clipped>"


def _safe_json_object(raw: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _retrieval_fallback_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "packet_id",
        "source_packet_id",
        "commit_sha",
        "source_commit_sha",
        "path",
        "qualified_name",
        "symbol_kind",
        "node_type",
        "subject",
        "statement",
        "summary",
        "selected_files",
        "selected_file_roles",
        "impact_roles",
        "impact_role",
        "primary_impact_role",
        "impact_role_counts",
        "selected_symbol_refs",
        "selected_code_refs",
        "reasoning_statements",
        "impact_count",
        "packet_ids",
        "commit_shas",
        "impact_ids",
        "reasons",
        "commit_messages",
        "promotion_grade",
        "policy",
        "hunk_count",
        "original_code_node_id",
        "evidence_refs",
        "repo_id",
        "job_id",
        "session_id",
    )
    return {key: metadata.get(key) for key in keep if metadata.get(key)}


def _fallback_doc_profile(kind: str) -> tuple[str, str, float]:
    if kind == "ReasoningNode":
        return "reasoning", "answer_grade_reasoning", 0.9
    if kind == "CodeImpactSummary":
        return "code_impact", "code_impact_summary", 0.82
    if kind == "FileImpactSummary":
        return "file_impact", "file_impact_summary", 0.84
    if kind == "Commit":
        return "commit", "work_change", 0.72
    if kind == "EvidenceRef":
        return "evidence", "supporting_evidence", 0.35
    if kind == "Packet":
        return "packet", "work_packet", 0.5
    if kind == "FileRef":
        return "file_ref", "code_support", 0.62
    if kind == "SymbolRef":
        return "symbol_ref", "code_support", 0.6
    if kind == "CodeRegionRef":
        return "code_region_ref", "code_support", 0.58
    return f"session_{kind.lower()}", "support", 0.5


def _qwen_contract(settings: Settings) -> dict[str, str]:
    payload = {
        "model": settings.qwen_model,
        "runtime": settings.qwen_runtime,
        "num_ctx": settings.qwen_num_ctx,
        "stage4_contract_hash": stage4_contract_hash(),
        "stage4_schema_hash": hashlib.sha256(json.dumps(stage4_output_schema(), sort_keys=True).encode("utf-8")).hexdigest(),
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
    }
    return {**payload, "contract_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()}


def _qwen_packet_key(packet: dict[str, Any], *, contract: dict[str, str]) -> dict[str, str]:
    return {
        "packet_id": str(packet.get("packet_id") or ""),
        "commit_sha": _packet_commit_sha(packet),
        "packet_hash": hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "contract_hash": str(contract.get("contract_hash") or ""),
    }


def _qwen_packet_cache_key(packet_key: dict[str, str]) -> tuple[str, str, str, str]:
    return (packet_key["packet_id"], packet_key["commit_sha"], packet_key["packet_hash"], packet_key["contract_hash"])


def _qwen_existing_results(output: Path) -> list[dict[str, Any]]:
    if not output.exists():
        return []
    try:
        loaded = _read_json(output)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _qwen_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _qwen_reusable_results(
    existing_results: list[dict[str, Any]],
    *,
    existing_manifest: dict[str, Any],
    packet_keys: list[dict[str, str]],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not existing_results:
        return {}
    manifest_packets = existing_manifest.get("packets")
    manifest_by_result_key: dict[tuple[str, str], tuple[str, str]] = {}
    if isinstance(manifest_packets, list):
        for item in manifest_packets:
            if not isinstance(item, dict):
                continue
            manifest_by_result_key[(str(item.get("packet_id") or ""), str(item.get("commit_sha") or ""))] = (
                str(item.get("packet_hash") or ""),
                str(item.get("contract_hash") or ""),
            )

    current_by_legacy_key = {(item["packet_id"], item["commit_sha"]): item for item in packet_keys}
    reusable: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for result in existing_results:
        packet_id = str(result.get("packet_id") or "")
        commit_sha = str(result.get("commit_sha") or "")
        if not packet_id:
            continue
        current = current_by_legacy_key.get((packet_id, commit_sha))
        if current is None:
            continue
        manifest_hash, manifest_contract = manifest_by_result_key.get((packet_id, commit_sha), ("", ""))
        if not manifest_hash or not manifest_contract:
            continue
        if manifest_hash != current["packet_hash"] or manifest_contract != current["contract_hash"]:
            continue
        if str(result.get("contract_hash") or manifest_contract) != current["contract_hash"]:
            continue
        if not isinstance(result.get("parsed_output"), dict):
            continue
        reusable[_qwen_packet_cache_key(current)] = result
    return reusable


def _write_qwen_checkpoint(
    output: Path,
    manifest: Path,
    results: list[dict[str, Any]],
    packet_keys: list[dict[str, str]],
    *,
    contract: dict[str, str],
    complete: bool,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output.with_suffix(output.suffix + ".tmp")
    manifest_tmp = manifest.with_suffix(manifest.suffix + ".tmp")
    output_tmp.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest_payload = {
        "complete": complete,
        "result_count": len(results),
        "packet_count": len(packet_keys),
        "contract": contract,
        "packets": packet_keys[: len(results)],
    }
    manifest_tmp.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    output_tmp.replace(output)
    manifest_tmp.replace(manifest)


def _packet_commit_sha(packet: dict[str, Any]) -> str:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    return str(commit.get("short_sha") or "")


def _packet_full_sha(packet: dict[str, Any]) -> str:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    return str(commit.get("full_sha") or commit.get("short_sha") or "")


def _packet_evidence_refs(packet: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("problem_refs", "rationale_refs", "validation_refs"):
        for item in packet.get(key, []) if isinstance(packet.get(key), list) else []:
            if isinstance(item, dict) and item.get("ref"):
                refs.append(str(item["ref"]))
    return list(dict.fromkeys(refs))


def _hunk_record(packet: dict[str, Any], hunk: dict[str, Any], *, index: int) -> dict[str, Any]:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    patch = str(hunk.get("patch") or "")
    return {
        "hunk_id": f"hunk:{packet.get('packet_id')}:{commit.get('short_sha')}:{index:04d}",
        "original_hunk_id": hunk.get("id"),
        "packet_id": packet.get("packet_id"),
        "commit_sha": commit.get("short_sha"),
        "full_sha": commit.get("full_sha"),
        "commit_message": commit.get("message"),
        "path": hunk.get("file_path"),
        "new_start": hunk.get("new_start"),
        "new_count": hunk.get("new_count"),
        "old_start": hunk.get("old_start"),
        "old_count": hunk.get("old_count"),
        "header": patch.splitlines()[0] if patch else "",
        "hunk_lines": patch.splitlines()[1:],
        "status": "M",
    }


def _code_node_record(packet: dict[str, Any], node: dict[str, Any], *, hunk_id_map: dict[str, str]) -> dict[str, Any]:
    commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    hunk_id = hunk_id_map.get(str(metadata.get("hunk_id") or ""), str(metadata.get("hunk_id") or ""))
    qualified_name = str(metadata.get("symbol_name") or metadata.get("structural_id") or node.get("ast_type") or "")
    content = str(node.get("content") or "")
    digest = hashlib.sha256(f"{packet.get('packet_id')}|{commit.get('short_sha')}|{node.get('id')}".encode("utf-8")).hexdigest()[:20]
    return {
        "code_node_id": f"code:{digest}",
        "packet_id": packet.get("packet_id"),
        "commit_sha": commit.get("short_sha"),
        "full_sha": commit.get("full_sha"),
        "commit_message": commit.get("message"),
        "path": node.get("file_path"),
        "node_source": node.get("ast_status"),
        "symbol_kind": metadata.get("symbol_kind") or node.get("ast_type"),
        "qualified_name": qualified_name,
        "line_start": node.get("line_start"),
        "line_end": node.get("line_end"),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:24],
        "text_excerpt": content[:1800],
        "hunk_ids": [hunk_id] if hunk_id else [],
        "mapped_hunk_count": 1 if hunk_id else 0,
    }


def _symbol_versions(code_nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for node in code_nodes:
        key = f"{node.get('path')}::{node.get('qualified_name')}"
        by_key.setdefault(key, []).append(node)
    symbols: list[dict[str, Any]] = []
    versions: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for key, nodes in sorted(by_key.items()):
        symbol_id = f"symbol:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"
        nodes = sorted(nodes, key=lambda item: (str(item.get("commit_sha") or ""), str(item.get("code_node_id") or "")))
        symbols.append(
            {
                "symbol_id": symbol_id,
                "symbol_key": key,
                "qualified_name": str(nodes[-1].get("qualified_name") or ""),
                "symbol_kind": str(nodes[-1].get("symbol_kind") or ""),
                "first_packet_id": str(nodes[0].get("packet_id") or ""),
                "latest_packet_id": str(nodes[-1].get("packet_id") or ""),
                "first_commit_sha": str(nodes[0].get("commit_sha") or ""),
                "latest_commit_sha": str(nodes[-1].get("commit_sha") or ""),
                "version_count": len(nodes),
            }
        )
        previous_version_id = ""
        for index, node in enumerate(nodes, start=1):
            version_seed = f"{symbol_id}|{node.get('code_node_id')}"
            version_id = f"version:{hashlib.sha256(version_seed.encode('utf-8')).hexdigest()[:20]}"
            versions.append(
                {
                    "version_id": version_id,
                    "symbol_id": symbol_id,
                    "code_node_id": node.get("code_node_id"),
                    "packet_id": node.get("packet_id"),
                    "commit_sha": node.get("commit_sha"),
                    "path": node.get("path"),
                    "qualified_name": node.get("qualified_name"),
                    "symbol_kind": node.get("symbol_kind"),
                    "version_index": index,
                }
            )
            edges.append({"from_id": symbol_id, "to_id": version_id, "kind": "SYMBOL_HAS_VERSION"})
            edges.append({"from_id": version_id, "to_id": symbol_id, "kind": "CODE_VERSION_OF_SYMBOL"})
            edges.append({"from_id": version_id, "to_id": str(node.get("code_node_id") or ""), "kind": "VERSION_CONTAINS_CODE_NODE"})
            edges.append({"from_id": f"commit:{node.get('commit_sha')}", "to_id": version_id, "kind": "COMMIT_HAS_CODE_VERSION"})
            if previous_version_id:
                edges.append({"from_id": previous_version_id, "to_id": version_id, "kind": "VERSION_SUPERSEDED_BY"})
            previous_version_id = version_id
    return symbols, versions, edges


def _relationship_edges(
    packets: list[dict[str, Any]],
    reasoning_nodes: list[dict[str, Any]],
    code_hunks: list[dict[str, Any]],
    code_nodes: list[dict[str, Any]],
    symbol_versions: dict[str, Any],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    packet_by_id = {str(packet.get("packet_id") or ""): packet for packet in packets}
    hunks_by_packet: dict[str, list[dict[str, Any]]] = {}
    nodes_by_packet: dict[str, list[dict[str, Any]]] = {}
    for hunk in code_hunks:
        hunks_by_packet.setdefault(str(hunk.get("packet_id") or ""), []).append(hunk)
    for node in code_nodes:
        nodes_by_packet.setdefault(str(node.get("packet_id") or ""), []).append(node)
    symbols = symbol_versions.get("symbols", []) if isinstance(symbol_versions.get("symbols"), list) else []
    versions = symbol_versions.get("code_versions", []) if isinstance(symbol_versions.get("code_versions"), list) else []
    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        commit_id = f"commit:{_packet_commit_sha(packet)}"
        for hunk in hunks_by_packet.get(packet_id, []):
            edges.append({"from_id": commit_id, "to_id": hunk.get("hunk_id"), "kind": "COMMIT_PRODUCED_HUNK"})
    for node in code_nodes:
        for hunk_id in node.get("hunk_ids", []) if isinstance(node.get("hunk_ids"), list) else []:
            edges.append({"from_id": hunk_id, "to_id": node.get("code_node_id"), "kind": "HUNK_MAPS_TO_CODE_NODE"})
    for node in reasoning_nodes:
        packet_id = str(node.get("source_packet_id") or "")
        node_id = str(node.get("reasoning_node_id") or node.get("node_id") or "")
        packet = packet_by_id.get(packet_id, {})
        edges.append({"from_id": node_id, "to_id": packet_id, "kind": "REASON_NODE_IN_PACKET"})
        edges.append({"from_id": node_id, "to_id": f"commit:{_packet_commit_sha(packet)}", "kind": "REASON_NODE_EXPLAINS_COMMIT"})
        for ref in node.get("evidence_refs", []) if isinstance(node.get("evidence_refs"), list) else []:
            edges.append({"from_id": node_id, "to_id": str(ref), "kind": "REASON_NODE_EVIDENCED_BY"})
        for hunk in hunks_by_packet.get(packet_id, [])[:12]:
            edges.append({"from_id": node_id, "to_id": hunk.get("hunk_id"), "kind": "REASON_NODE_LINKED_TO_HUNK"})
        for code_node in nodes_by_packet.get(packet_id, [])[:12]:
            edges.append({"from_id": node_id, "to_id": code_node.get("code_node_id"), "kind": "REASON_NODE_LINKED_TO_CODE_NODE"})
    for symbol in symbols:
        for node in reasoning_nodes:
            if str(node.get("source_packet_id") or "") == str(symbol.get("latest_packet_id") or symbol.get("first_packet_id") or ""):
                edges.append({"from_id": node.get("node_id"), "to_id": symbol.get("symbol_id"), "kind": "REASON_NODE_LINKED_TO_SYMBOL"})
    for version in versions:
        for node in reasoning_nodes:
            if str(node.get("source_packet_id") or "") == str(version.get("packet_id") or ""):
                edges.append({"from_id": node.get("node_id"), "to_id": version.get("version_id"), "kind": "REASON_NODE_LINKED_TO_CODE_VERSION"})
    edges.extend(symbol_versions.get("edges", []) if isinstance(symbol_versions.get("edges"), list) else [])
    return [edge for edge in edges if edge.get("from_id") and edge.get("to_id") and edge.get("kind")]


def _versioned_items(value: Any, job: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                **item,
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "session_id": job.get("session_id"),
                "job_id": job.get("job_id"),
                "repo_id": job.get("repo_id") or "",
                "repo_path": job.get("repo_path") or "",
            }
        )
    return out


def _job_repo_id(job: dict[str, Any]) -> str:
    return str(job.get("repo_id") or "") or resolve_repo_identity(str(job.get("repo_path") or "")).repo_id


def _quality_issues(
    *,
    central_result: dict[str, Any],
    retrieval_result: dict[str, Any],
    embedding_result: dict[str, Any],
    faiss_result: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    central_status = str(central_result.get("status") or "")
    central_mode = str(central_result.get("mode") or "")
    if central_status != "applied" or central_mode != "apply_exact_atoms":
        issues.append(
            {
                "code": "central_merge_not_applied",
                "message": "central_version_merge produced a dry-run plan, not applied central memory",
                "status": central_status,
                "mode": central_mode,
                "plan_id": central_result.get("plan_id") or "",
            }
        )
    if central_result.get("stale_merge_result"):
        issues.append(
            {
                "code": "central_merge_result_stale",
                "message": "central_version_merge has a stale merge_result.json for a different plan",
                "plan_id": central_result.get("plan_id") or "",
                "stale_merge_result": central_result.get("stale_merge_result"),
            }
        )
    if central_status == "applied" and str(central_result.get("input_source") or "") != "curated_graph_manifest":
        issues.append(
            {
                "code": "central_consumed_non_curated_input",
                "message": "central_version_merge did not apply from curated_graph_manifest",
                "input_source": central_result.get("input_source") or "",
            }
        )
    if central_status == "applied" and not str(central_result.get("curated_input_hash") or ""):
        issues.append({"code": "central_missing_curated_input_hash", "message": "central apply lacks curated input hash"})
    if central_result.get("existing_atom_scan_error"):
        issues.append(
            {
                "code": "central_atom_scan_failed",
                "message": "central_version_merge could not scan existing canonical atoms",
                "error": central_result.get("existing_atom_scan_error"),
            }
        )

    doc_count = int(retrieval_result.get("doc_count") or 0)
    if doc_count <= 0:
        issues.append({"code": "retrieval_docs_empty", "message": "retrieval_docs produced no documents"})
    retrieval_source = str(retrieval_result.get("retrieval_source") or "")
    if retrieval_source in {"compact_manifest_fallback", "curated_graph_manifest_missing"}:
        issues.append(
            {
                "code": "retrieval_fallback_or_missing_curated",
                "message": "retrieval_docs did not use curated product input",
                "retrieval_source": retrieval_source,
            }
        )
    if doc_count > 0 and not str(retrieval_result.get("active_projection_id") or ""):
        issues.append({"code": "active_projection_missing", "message": "retrieval docs exist but no active projection was recorded"})
    activation_gate = retrieval_result.get("activation_gate") if isinstance(retrieval_result.get("activation_gate"), dict) else {}
    if doc_count > 0 and activation_gate and activation_gate.get("passed") is not True:
        issues.append(
            {
                "code": "retrieval_projection_activation_gate_failed",
                "message": "retrieval projection failed semantic activation gates",
                "blocking_failures": activation_gate.get("blocking_failures") or [],
            }
        )

    total_docs = int(embedding_result.get("total_docs") or doc_count or 0)
    embedded = int(embedding_result.get("embedded") or 0)
    already = int(embedding_result.get("already_embedded") or 0)
    covered = embedded + already
    if total_docs and covered < total_docs:
        issues.append(
            {
                "code": "embedding_coverage_partial",
                "message": "not all retrieval documents have active embeddings",
                "covered_docs": covered,
                "total_docs": total_docs,
                "limit_hit": bool(embedding_result.get("limit_hit")),
            }
        )

    faiss_status = str(faiss_result.get("status") or "")
    faiss_items = int(faiss_result.get("item_count") or 0)
    if total_docs and faiss_items < total_docs:
        issues.append(
            {
                "code": "faiss_coverage_partial",
                "message": "FAISS cache does not cover all retrieval documents",
                "item_count": faiss_items,
                "total_docs": total_docs,
                "status": faiss_status,
            }
        )
    if total_docs and faiss_items > total_docs:
        issues.append(
            {
                "code": "faiss_coverage_stale",
                "message": "FAISS cache includes vectors outside active retrieval document coverage",
                "item_count": faiss_items,
                "total_docs": total_docs,
                "status": faiss_status,
            }
        )
    return issues


def _quality_readiness(
    *,
    issues: list[dict[str, Any]],
    central_result: dict[str, Any],
    retrieval_result: dict[str, Any],
    embedding_result: dict[str, Any],
    faiss_result: dict[str, Any],
) -> dict[str, bool]:
    issue_codes = {str(issue.get("code") or "") for issue in issues}
    doc_count = int(retrieval_result.get("doc_count") or 0)
    total_docs = int(embedding_result.get("total_docs") or doc_count or 0)
    embedded = int(embedding_result.get("embedded") or 0)
    already = int(embedding_result.get("already_embedded") or 0)
    covered = embedded + already
    faiss_items = int(faiss_result.get("item_count") or 0)
    central_memory_ready = (
        str(central_result.get("status") or "") == "applied"
        and str(central_result.get("mode") or "") == "apply_exact_atoms"
        and str(central_result.get("input_source") or "") == "curated_graph_manifest"
        and bool(str(central_result.get("curated_input_hash") or ""))
    )
    lexical_retrieval_ready = (
        doc_count > 0
        and str(retrieval_result.get("retrieval_source") or "") in {"curated_graph_manifest", "central_active_graph_view"}
        and bool(str(retrieval_result.get("active_projection_id") or ""))
    )
    vector_retrieval_ready = bool(total_docs) and covered >= total_docs and faiss_items == total_docs
    answer_trace_ready = central_memory_ready and lexical_retrieval_ready
    product_ready = central_memory_ready and lexical_retrieval_ready and vector_retrieval_ready and answer_trace_ready and not issue_codes
    return {
        "mechanical_complete": True,
        "lexical_retrieval_ready": lexical_retrieval_ready,
        "vector_retrieval_ready": vector_retrieval_ready,
        "central_memory_ready": central_memory_ready,
        "answer_trace_ready": answer_trace_ready,
        "product_ready": product_ready,
    }


def _evidence_ref_nodes(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for packet in packets:
        for group, kind in (("problem_refs", "problem"), ("rationale_refs", "rationale"), ("validation_refs", "validation")):
            for ref in packet.get(group, []) if isinstance(packet.get(group), list) else []:
                if not isinstance(ref, dict) or not ref.get("ref"):
                    continue
                refs[str(ref["ref"])] = {
                    "evidence_ref_id": ref["ref"],
                    "ref": ref["ref"],
                    "packet_id": packet.get("packet_id"),
                    "commit_sha": _packet_commit_sha(packet),
                    "evidence_kind": kind,
                    "excerpt": ref.get("excerpt") or ref.get("output_preview") or ref.get("command") or "",
                }
    return list(refs.values())


def _commit_nodes(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = []
    for packet in packets:
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        nodes.append(
            {
                "commit_node_id": f"commit:{commit.get('short_sha')}",
                "packet_id": packet.get("packet_id"),
                "short_sha": commit.get("short_sha"),
                "full_sha": commit.get("full_sha"),
                "message": commit.get("message"),
                "changed_files_count": commit.get("changed_files_count"),
            }
        )
    return nodes


def _upsert_compact_graph(store: GraphStore, nodes: tuple[dict[str, Any], ...], edges: tuple[dict[str, Any], ...], *, job: dict[str, Any]) -> dict[str, Any]:
    namespace = str(job["job_id"]).rsplit(":", 1)[-1][:12]
    id_map = {str(node["id"]): f"{namespace}:{node['id']}" for node in nodes}
    node_write_count = 0
    for node in nodes:
        metadata = json.loads(str(node.get("properties_json") or "{}"))
        metadata.update(
            {
                "original_node_id": node.get("id"),
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "job_id": job.get("job_id"),
                "repo_id": job.get("repo_id"),
                "immutable_session_graph_node": True,
            }
        )
        store.upsert_node(
            GraphNode(
                id=id_map[str(node["id"])],
                kind=str(node.get("kind") or ""),
                label=str(node.get("label") or ""),
                summary=str(node.get("summary") or ""),
                status="active",
                scope="central",
                session_id=str(job.get("session_id") or ""),
                source_app=str(job.get("source_app") or ""),
                commit_id=str(node.get("commit_sha") or ""),
                metadata=metadata,
            )
        )
        node_write_count += 1
    max_edges = _central_session_edge_write_limit()
    if max_edges >= 0 and len(edges) > max_edges:
        return {
            "node_write_count": node_write_count,
            "edge_write_count": 0,
            "edge_write_skipped": True,
            "edge_write_limit": max_edges,
            "edge_count": len(edges),
        }
    edge_write_count = 0
    for index, edge in enumerate(edges):
        source = id_map.get(str(edge.get("from_id") or ""))
        target = id_map.get(str(edge.get("to_id") or ""))
        if not source or not target:
            continue
        metadata = json.loads(str(edge.get("properties_json") or "{}"))
        metadata["immutable_session_graph_edge"] = True
        store.upsert_edge(
            GraphEdge(
                id=f"v2edge:{namespace}:{index:06d}",
                source_id=source,
                target_id=target,
                kind=str(edge.get("kind") or ""),
                confidence=1.0,
                metadata=metadata,
            )
        )
        edge_write_count += 1
    return {
        "node_write_count": node_write_count,
        "edge_write_count": edge_write_count,
        "edge_write_skipped": False,
        "edge_write_limit": max_edges,
        "edge_count": len(edges),
    }


def _write_curated_session_graph_to_central(
    graph_store_factory: Callable[[Path], GraphStore],
    graph_path: Path,
    *,
    nodes: tuple[dict[str, Any], ...],
    edges: tuple[dict[str, Any], ...],
    job: dict[str, Any],
) -> dict[str, Any]:
    central = graph_store_factory(graph_path)
    try:
        central.init_schema()
        result = _upsert_compact_graph(central, nodes, edges, job=job)
        return {"status": "applied", **result}
    except Exception as exc:
        return {
            "status": "failed_recoverable",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "node_write_count": 0,
            "edge_write_count": 0,
            "edge_write_skipped": True,
            "edge_write_limit": _central_session_edge_write_limit(),
            "edge_count": len(edges),
            "curated_manifest_still_available": True,
        }
    finally:
        central.close()
