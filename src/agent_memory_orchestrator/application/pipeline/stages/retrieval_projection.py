from __future__ import annotations

import json
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from ....core.db import connect
from ....infrastructure.faiss.embedding_store import GraphEmbeddingStore
from ....application.services.retrieval.embedding import RETRIEVAL_EMBEDDING_KIND
from ....domain.retrieval.models import RetrievalDocument
from ....infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ....domain.retrieval.projection import build_retrieval_documents_from_graph
from ....application.services.retrieval.embedding import embed_missing_retrieval_documents
from ....infrastructure.llm.text_embedder import StrictTextEmbedder
from ....domain.pipeline.constants import RESET_MARKER_KEY
from ....domain.pipeline.constants import RETRIEVAL_PROJECTION_VERSION
from ..job_runner import PendingModel
from ..job_runner import StageFailed
from ..job_runner import StageResult
from ..job_runner import _job_repo_id
from ..job_runner import _optional_product_manifest_info
from ..job_runner import require_complete_production_marker
from ..quality_gates import _central_merge_quality_result
from ..quality_gates import _quality_issues
from ..quality_gates import _quality_readiness
from ..retrieval_projection_helpers import _merge_cumulative_retrieval_docs
from ..retrieval_projection_helpers import _retrieval_doc_content_hash
from ..retrieval_projection_helpers import _retrieval_documents_from_manifest
from ..retrieval_projection_helpers import _retrieval_projection_activation_gate
from ..retrieval_projection_helpers import _retrieval_projection_id
from ..stage_artifacts import _read_json
from ..stage_artifacts import _stage_output
from ....infrastructure.kuzu.central_graph import repo_central_graph_path


def run_retrieval_docs_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    require_complete_production_marker(runner.job_store.marker(RESET_MARKER_KEY))
    repo_id = _job_repo_id(job)
    manifest_info = _optional_product_manifest_info(Path(str(job["artifact_dir"])))
    conn = connect(runner.settings.retrieval_db_path)
    try:
        index = RetrievalIndexStore(conn)
        graph_error = ""
        if manifest_info.get("curated_manifest_exists"):
            current_docs = _retrieval_documents_from_manifest(
                manifest_path=Path(str(manifest_info["curated_manifest_path"])),
                source="curated_graph_manifest",
                job=job,
                repo_id=repo_id,
                max_doc_chars=runner.settings.auto_retrieval_max_doc_chars,
                limit=runner.settings.auto_retrieval_node_limit,
            )
            central_docs, central_graph_error = central_active_retrieval_docs(runner, repo_id=repo_id)
            current_docs = [*current_docs, *central_docs]
            retrieval_source = "curated_graph_manifest"
            if central_graph_error:
                graph_error = central_graph_error
        else:
            current_docs = []
            retrieval_source = "curated_graph_manifest_missing"
            graph_error = "curated_graph_manifest_missing"
        existing_docs = index.list_repo_documents_all(repo_id=repo_id) if retrieval_source == "curated_graph_manifest" else []
        docs = _merge_cumulative_retrieval_docs(existing_docs=existing_docs, current_docs=current_docs)
        doc_content_hash = _retrieval_doc_content_hash(docs)
        projection_id = _retrieval_projection_id(
            repo_id=repo_id,
            projection_version=RETRIEVAL_PROJECTION_VERSION,
            source_artifact_hash=str(manifest_info.get("curated_input_hash") or ""),
            doc_content_hash=doc_content_hash,
        )
        projection: dict[str, Any] = {}
        activation_gate: dict[str, Any] = {
            "passed": False,
            "blocking_failures": ["retrieval_projection_no_docs"],
            "summary": {},
        }
        if retrieval_source == "curated_graph_manifest" and docs:
            activation_gate = _retrieval_projection_activation_gate(docs)
            projection = index.upsert_projection(
                projection_id=projection_id,
                repo_id=repo_id,
                projection_version=RETRIEVAL_PROJECTION_VERSION,
                source_artifact_hash=str(manifest_info.get("curated_input_hash") or ""),
                doc_content_hash=doc_content_hash,
                status="building",
                metadata={"retrieval_source": retrieval_source, "activation_gate": activation_gate, **manifest_info},
            )
            index.replace_projection_documents(docs, repo_id=repo_id, projection_id=projection_id)
            if activation_gate["passed"]:
                index.set_projection_status(projection_id, "validated")
                projection = index.activate_projection(repo_id=repo_id, projection_id=projection_id)
            else:
                index.set_projection_status(projection_id, "review_required")
                projection = index.projection(projection_id) or {}
    finally:
        conn.close()
    output = stage_dir / "retrieval_docs_result.json"
    payload = {
        "doc_count": len(docs),
        "repo_id": repo_id,
        "retrieval_source": retrieval_source,
        "graph_error": graph_error,
        "projection_id": projection_id,
        "projection_version": RETRIEVAL_PROJECTION_VERSION,
        "projection_status": projection.get("status"),
        "active_projection_id": projection.get("projection_id") if activation_gate["passed"] else "",
        "activation_gate": activation_gate,
        "current_doc_count": len(current_docs),
        "central_active_doc_count": len(
            [doc for doc in current_docs if doc.metadata.get("source") == "central_active_graph_view"]
        ),
        "carried_forward_doc_count": max(0, len(docs) - len(current_docs)),
        "doc_content_hash": doc_content_hash,
        **manifest_info,
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return StageResult(output_path=output, diagnostics=payload)


def central_active_retrieval_docs(runner: Any, *, repo_id: str) -> tuple[list[RetrievalDocument], str]:
    graph = runner.graph_store_factory(repo_central_graph_path(runner.settings, repo_id))
    try:
        graph.init_schema()
        docs = build_retrieval_documents_from_graph(
            graph,
            node_limit=runner.settings.auto_retrieval_node_limit,
            max_doc_chars=runner.settings.auto_retrieval_max_doc_chars,
            pipeline_version="",
            graph_schema_version="",
            repo_id=repo_id,
        )
    except Exception as exc:  # pragma: no cover - central graph may be locked by another process
        return [], f"central_active_graph_view_scan_failed:{type(exc).__name__}:{exc}"
    finally:
        graph.close()
    central_docs = [
        dataclass_replace(
            doc,
            metadata={**doc.metadata, "source": "central_active_graph_view", "repo_id": repo_id},
        )
        for doc in docs
        if doc.doc_type in {"central_version", "central_atom", "graph_lineage"}
    ]
    return central_docs, ""


def run_embeddings_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    repo_id = _job_repo_id(job)
    conn = connect(runner.settings.retrieval_db_path)
    try:
        index = RetrievalIndexStore(conn)
        embedding_store = GraphEmbeddingStore(conn, db_path=runner.settings.retrieval_db_path)
        try:
            embedder = StrictTextEmbedder(runner.settings.embedding_model, dims=runner.settings.embedding_dims)
        except RuntimeError as exc:
            raise PendingModel(
                "embedding_model_unavailable",
                {"error": str(exc), "model": runner.settings.embedding_model},
            ) from exc
        result = embed_missing_retrieval_documents(
            index_store=index,
            embedding_store=embedding_store,
            embedder=embedder,
            model=runner.settings.embedding_model,
            graph_scope="v2",
            session_id=str(job["session_id"]),
            repo_id=repo_id,
            extraction_run_id=str(job["job_id"]),
            limit=runner.settings.auto_embedding_batch_size,
            embedding_kind=RETRIEVAL_EMBEDDING_KIND,
        )
    finally:
        conn.close()
    output = stage_dir / "embeddings_result.json"
    payload = {**result.as_dict(), "repo_id": repo_id}
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return StageResult(output_path=output, diagnostics=payload)


def run_faiss_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    repo_id = _job_repo_id(job)
    conn = connect(runner.settings.retrieval_db_path)
    try:
        index = RetrievalIndexStore(conn)
        active_docs = index.list_documents(limit=100000, repo_id=repo_id)
        embedding_store = GraphEmbeddingStore(conn, db_path=runner.settings.retrieval_db_path)
        result = embedding_store.build_faiss_cache(
            embedding_kind=RETRIEVAL_EMBEDDING_KIND,
            model=runner.settings.embedding_model,
            graph_scope="v2",
            graph_paths={doc.doc_id for doc in active_docs},
        )
    finally:
        conn.close()
    output = stage_dir / "faiss_result.json"
    payload = {**result.as_dict(), "repo_id": repo_id, "active_projection_doc_count": len(active_docs)}
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return StageResult(output_path=output, diagnostics=payload)


def run_quality_eval_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del job, runner
    session_graph_result = _read_json(_stage_output(artifact_dir, "kuzu_write"))
    central_result = _central_merge_quality_result(artifact_dir)
    retrieval_result = _read_json(_stage_output(artifact_dir, "retrieval_docs"))
    embedding_result = _read_json(_stage_output(artifact_dir, "embeddings"))
    faiss_result = _read_json(_stage_output(artifact_dir, "faiss"))
    issues = _quality_issues(
        central_result=central_result if isinstance(central_result, dict) else {},
        retrieval_result=retrieval_result if isinstance(retrieval_result, dict) else {},
        embedding_result=embedding_result if isinstance(embedding_result, dict) else {},
        faiss_result=faiss_result if isinstance(faiss_result, dict) else {},
    )
    readiness = _quality_readiness(
        issues=issues,
        central_result=central_result if isinstance(central_result, dict) else {},
        retrieval_result=retrieval_result if isinstance(retrieval_result, dict) else {},
        embedding_result=embedding_result if isinstance(embedding_result, dict) else {},
        faiss_result=faiss_result if isinstance(faiss_result, dict) else {},
    )
    output = stage_dir / "quality_eval.json"
    payload = {
        "ok": not issues,
        **readiness,
        "blocking_issues": issues,
        "session_graph_write": session_graph_result,
        "kuzu": session_graph_result,
        "central_version_merge": central_result,
        "retrieval_docs": retrieval_result,
        "embeddings": embedding_result,
        "faiss": faiss_result,
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if issues:
        raise StageFailed("quality_eval_product_readiness_failed", payload)
    return StageResult(output_path=output, diagnostics=payload)
