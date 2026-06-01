from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager

from ...core.config import Settings
from ...infrastructure.kuzu import GraphEdge
from ...infrastructure.kuzu import GraphNode
from ...infrastructure.kuzu import GraphStore
from ...infrastructure.kuzu import KuzuGraphStore
from ...llm.qwen import OllamaQwenClient as OllamaQwenClient  # noqa: F401
from ...llm.qwen import QwenUnavailable as QwenUnavailable  # noqa: F401
from ...domain.versioning.repo_identity import resolve_repo_identity
from ...domain.retrieval.models import RetrievalDocument
from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from ...domain.pipeline.constants import PRODUCTION_STAGES
from ...infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from .graph_records import _code_node_record as _code_node_record
from .graph_records import _commit_nodes as _commit_nodes
from .graph_records import _evidence_ref_nodes as _evidence_ref_nodes
from .graph_records import _hunk_record as _hunk_record
from .graph_records import _relationship_edges as _relationship_edges
from .graph_records import _symbol_versions as _symbol_versions
from .graph_records import _versioned_items as _versioned_items
from .packet_helpers import _packet_commit_sha as _packet_commit_sha
from .packet_helpers import _packet_evidence_refs as _packet_evidence_refs
from .packet_helpers import _packet_full_sha as _packet_full_sha
from .quality_gates import _central_merge_quality_result as _central_merge_quality_result
from .quality_gates import _quality_issues as _quality_issues
from .quality_gates import _quality_readiness as _quality_readiness
from .qwen_checkpoint import _qwen_contract as _qwen_contract
from .qwen_checkpoint import _qwen_existing_manifest as _qwen_existing_manifest
from .qwen_checkpoint import _qwen_existing_results as _qwen_existing_results
from .qwen_checkpoint import _qwen_packet_cache_key as _qwen_packet_cache_key
from .qwen_checkpoint import _qwen_packet_key as _qwen_packet_key
from .qwen_checkpoint import _qwen_reusable_results as _qwen_reusable_results
from .qwen_checkpoint import _write_qwen_checkpoint as _write_qwen_checkpoint
from .retrieval_projection_helpers import _merge_cumulative_retrieval_docs as _merge_cumulative_retrieval_docs
from .retrieval_projection_helpers import _retrieval_doc_content_hash as _retrieval_doc_content_hash
from .retrieval_projection_helpers import _retrieval_documents_from_manifest as _retrieval_documents_from_manifest
from .retrieval_projection_helpers import _retrieval_projection_activation_gate as _retrieval_projection_activation_gate
from .retrieval_projection_helpers import _retrieval_projection_id as _retrieval_projection_id
from .stage_artifacts import file_sha256
from .stage_artifacts import path_hash
from .stage_config import stage_config_hash
from .stage_config import stage_config_payload as stage_config_payload


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
        owner = f"production-runner:{uuid.uuid4().hex}"
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
        if stage == PRODUCTION_STAGES[0]:
            return self.settings.evidence_dir
        previous = PRODUCTION_STAGES[PRODUCTION_STAGES.index(stage) - 1]
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
        from .stages.code_graph import run_git_hunks_stage

        return run_git_hunks_stage(self, job, artifact_dir, stage_dir)

    def _stage_ast_code_nodes(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.code_graph import run_ast_code_nodes_stage

        return run_ast_code_nodes_stage(self, job, artifact_dir, stage_dir)

    def _stage_symbol_versions(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.code_graph import run_symbol_versions_stage

        return run_symbol_versions_stage(self, job, artifact_dir, stage_dir)

    def _stage_reasoning_code_links(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        from .stages.code_graph import run_reasoning_code_links_stage

        return run_reasoning_code_links_stage(self, job, artifact_dir, stage_dir)

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


def require_complete_production_marker(marker: dict[str, Any] | None) -> dict[str, Any]:
    if marker is None:
        raise RuntimeError("production_marker_missing")
    cleaned = marker.get("cleaned") if isinstance(marker.get("cleaned"), dict) else {}
    validated = marker.get("validated") if isinstance(marker.get("validated"), dict) else {}
    if marker.get("pipeline_version") != PIPELINE_VERSION or marker.get("graph_schema_version") != GRAPH_SCHEMA_VERSION:
        raise RuntimeError("production_marker_version_mismatch")
    cleaned_ok = cleaned.get("graph") is True and cleaned.get("retrieval") is True
    adopted_ok = (
        marker.get("adopted_existing_production") is True
        and validated.get("graph") is True
        and validated.get("retrieval") is True
    )
    if not cleaned_ok and not adopted_ok:
        raise RuntimeError("production_marker_incomplete")
    return marker


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
    if stage in PRODUCTION_STAGES:
        return stage
    return PRODUCTION_STAGES[0]


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


def _job_repo_id(job: dict[str, Any]) -> str:
    return str(job.get("repo_id") or "") or resolve_repo_identity(str(job.get("repo_path") or "")).repo_id


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
