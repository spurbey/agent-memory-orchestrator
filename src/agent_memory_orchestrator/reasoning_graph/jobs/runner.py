from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.config import Settings
from ...core.db import connect
from ...graph.store import GraphEdge
from ...graph.store import GraphNode
from ...graph.store import GraphStore
from ...graph.store import KuzuGraphStore
from ...llm.qwen import OllamaQwenClient
from ...llm.qwen import QwenUnavailable
from ..code_analysis import extract_code_nodes_from_commit
from ..embedding_store import GraphEmbeddingStore
from ..evidence_view import build_reasoning_evidence_view
from ..evidence_view import write_reasoning_evidence_view_artifacts
from ..reasoning_extraction import review_reasoning_extraction_results
from ..retrieval import RETRIEVAL_EMBEDDING_KIND
from ..retrieval import RetrievalIndexStore
from ..retrieval import build_retrieval_documents_from_graph
from ..retrieval import embed_missing_retrieval_documents
from ..session_graph_writer import build_compact_session_graph
from ..session_graph_writer import write_compact_session_graph
from ..session_runtime import StrictTextEmbedder
from ..stage4_contract import build_stage4_packet_prompt
from ..stage4_contract import stage4_contract_hash
from ..stage4_contract import stage4_output_schema
from ..work_packets import build_reasoning_work_packets_from_view
from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import RESET_MARKER_KEY
from .constants import V2_STAGES
from .store import V2SessionJobStore


StageFn = Callable[[dict[str, Any], Path], dict[str, Any]]


@dataclass(slots=True, frozen=True)
class StageResult:
    output_path: Path
    diagnostics: dict[str, Any]


class V2SessionJobRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        job_store: V2SessionJobStore | None = None,
        graph_store_factory: Callable[[Path], GraphStore] = KuzuGraphStore,
    ) -> None:
        self.settings = settings
        self.job_store = job_store or V2SessionJobStore(settings)
        self.graph_store_factory = graph_store_factory

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
        input_hash = path_hash(input_artifact)
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
        self.job_store.start_stage(
            job_id=str(job["job_id"]),
            stage=stage,
            input_artifact=str(input_artifact),
            input_hash=input_hash,
            stage_config_hash=config_hash,
        )
        return getattr(self, f"_stage_{stage}")(job, artifact_dir, stage_dir)

    def _stage_input_artifact(self, job: dict[str, Any], stage: str, artifact_dir: Path) -> Path:
        if stage == V2_STAGES[0]:
            return self.settings.evidence_dir
        previous = V2_STAGES[V2_STAGES.index(stage) - 1]
        row = self.job_store.stage_row(job_id=str(job["job_id"]), stage=previous)
        if row is None or not row.get("output_artifact"):
            raise RuntimeError(f"missing_previous_stage:{previous}")
        return Path(str(row["output_artifact"]))

    def _stage_evidence_view(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        session_jsonl = stage_dir / "session_raw_evidence.jsonl"
        records = _session_records(self.settings.evidence_dir, str(job["session_id"]))
        if not records:
            raise RuntimeError(f"no_raw_evidence_for_session:{job['session_id']}")
        _write_jsonl(session_jsonl, records)
        transcript_path = _first_transcript_path(records)
        build = build_reasoning_evidence_view(
            session_jsonl,
            transcript_path=Path(transcript_path) if transcript_path else None,
            repo_root=Path(str(job.get("repo_path") or ".")).resolve(),
        )
        write_reasoning_evidence_view_artifacts(build, stage_dir)
        output = stage_dir / "reasoning_evidence_view.json"
        return StageResult(
            output_path=output,
            diagnostics={"raw_record_count": len(records), "quality": build.quality},
        )

    def _stage_work_packets(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        del artifact_dir
        view = _read_json(self._stage_input_artifact(job, "work_packets", Path(str(job["artifact_dir"]))))
        build = build_reasoning_work_packets_from_view(view)
        output = stage_dir / "reasoning_work_packets.json"
        output.write_text(json.dumps(list(build.packets), indent=2, ensure_ascii=False), encoding="utf-8")
        (stage_dir / "packet_quality_inventory.json").write_text(json.dumps(build.quality, indent=2, ensure_ascii=False), encoding="utf-8")
        (stage_dir / "quarantined_commits.json").write_text(json.dumps(list(build.quarantined_commits), indent=2), encoding="utf-8")
        if build.quality.get("stage_acceptance") != "PASS":
            reason = "no_commit_backed_work_packets" if not build.packets else "work_packets_acceptance_failed"
            raise StageFailed(
                reason,
                {
                    "quality": build.quality,
                    "packet_artifact": str(output),
                    "quarantined_commit_count": len(build.quarantined_commits),
                    "note": "V2 answer-grade graph output requires at least one resolved Git commit-backed work packet.",
                },
            )
        return StageResult(output_path=output, diagnostics={"quality": build.quality})

    def _stage_qwen_reasoning(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        packets = _read_json(self._stage_input_artifact(job, "qwen_reasoning", artifact_dir))
        if not isinstance(packets, list):
            raise RuntimeError("work_packets_output_must_be_list")
        client = OllamaQwenClient(
            endpoint=self.settings.qwen_endpoint,
            model=self.settings.qwen_model,
            timeout_seconds=self.settings.qwen_extract_timeout_seconds,
            num_ctx=self.settings.qwen_num_ctx,
        )
        results: list[dict[str, Any]] = []
        for packet in packets:
            prompt = build_stage4_packet_prompt(packet)
            try:
                parsed = client.generate_json(
                    prompt,
                    num_predict=900,
                    timeout_seconds=self.settings.qwen_extract_timeout_seconds,
                    schema=stage4_output_schema(),
                )
            except QwenUnavailable as exc:
                raise PendingModel("qwen_unavailable", {"packet_id": packet.get("packet_id"), "error": str(exc)}) from exc
            results.append(
                {
                    "packet_id": packet.get("packet_id"),
                    "commit_sha": _packet_commit_sha(packet),
                    "model": self.settings.qwen_model,
                    "runtime": self.settings.qwen_runtime,
                    "parsed_output": parsed,
                }
            )
        output = stage_dir / "stage4_packet_reasoning_results.json"
        output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"packet_count": len(packets), "result_count": len(results)})

    def _stage_reasoning_review(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        packets = _read_json(_stage_output(artifact_dir, "work_packets"))
        results = _read_json(self._stage_input_artifact(job, "reasoning_review", artifact_dir))
        review = review_reasoning_extraction_results(
            packets=packets if isinstance(packets, list) else [],
            results=results if isinstance(results, list) else [],
            source_name="v2_session_job",
        )
        (stage_dir / "stage4_reasoning_review.json").write_text(json.dumps(review.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        output = stage_dir / "accepted_reasoning_nodes.json"
        output.write_text(json.dumps(list(review.accepted_nodes), indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"summary": review.summary})

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
        require_complete_v2_reset_marker(self.job_store.marker(RESET_MARKER_KEY))
        packets = _versioned_items(_read_json(_stage_output(artifact_dir, "work_packets")), job)
        reasoning_nodes = _versioned_items(_read_json(_stage_output(artifact_dir, "reasoning_review")), job)
        hunk_nodes = _versioned_items(_read_json(_stage_output(artifact_dir, "git_hunks")), job)
        code_nodes = _versioned_items(_read_json(_stage_output(artifact_dir, "ast_code_nodes")), job)
        symbol_versions = _read_json(_stage_output(artifact_dir, "symbol_versions"))
        symbols = _versioned_items(symbol_versions.get("symbols", []) if isinstance(symbol_versions, dict) else [], job)
        versions = _versioned_items(symbol_versions.get("code_versions", []) if isinstance(symbol_versions, dict) else [], job)
        raw_edges = _read_json(_stage_output(artifact_dir, "reasoning_code_links"))
        evidence_refs = _versioned_items(_evidence_ref_nodes(packets), job)
        commits = _versioned_items(_commit_nodes(packets), job)
        graph = build_compact_session_graph(
            packets=packets,
            reasoning_nodes=reasoning_nodes,
            evidence_refs=evidence_refs,
            commit_nodes=commits,
            code_hunks=hunk_nodes,
            code_nodes=code_nodes,
            symbols=symbols,
            code_versions=versions,
            raw_edges=raw_edges if isinstance(raw_edges, list) else [],
        )
        manifest = stage_dir / "compact_graph_manifest.json"
        manifest.write_text(json.dumps(graph.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        artifact_graph_path = stage_dir / "session_graph.kuzu"
        write_compact_session_graph(graph_path=artifact_graph_path, nodes=list(graph.nodes), edges=list(graph.edges), force=True)
        central = self.graph_store_factory(self.settings.graph_path)
        try:
            central.init_schema()
            _upsert_compact_graph(central, graph.nodes, graph.edges, job=job)
        finally:
            central.close()
        output = stage_dir / "kuzu_write_result.json"
        output.write_text(
            json.dumps(
                {
                    "ok": graph.inventory.get("unresolved_edge_count") == 0,
                    "graph_path": str(self.settings.graph_path),
                    "artifact_graph_path": str(artifact_graph_path),
                    "inventory": graph.inventory,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return StageResult(output_path=output, diagnostics=graph.inventory)

    def _stage_retrieval_docs(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        del artifact_dir
        require_complete_v2_reset_marker(self.job_store.marker(RESET_MARKER_KEY))
        graph = self.graph_store_factory(self.settings.graph_path)
        conn = connect(self.settings.retrieval_db_path)
        try:
            index = RetrievalIndexStore(conn)
            docs = build_retrieval_documents_from_graph(
                graph,
                session_id="",
                node_limit=self.settings.auto_retrieval_node_limit,
                max_doc_chars=self.settings.auto_retrieval_max_doc_chars,
                pipeline_version=PIPELINE_VERSION,
                graph_schema_version=GRAPH_SCHEMA_VERSION,
            )
            index.replace_documents(docs)
        finally:
            graph.close()
            conn.close()
        output = stage_dir / "retrieval_docs_result.json"
        output.write_text(json.dumps({"doc_count": len(docs)}, indent=2), encoding="utf-8")
        return StageResult(output_path=output, diagnostics={"doc_count": len(docs)})

    def _stage_embeddings(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        del artifact_dir
        conn = connect(self.settings.retrieval_db_path)
        try:
            index = RetrievalIndexStore(conn)
            embedding_store = GraphEmbeddingStore(conn, db_path=self.settings.retrieval_db_path)
            try:
                embedder = StrictTextEmbedder(self.settings.embedding_model)
            except RuntimeError as exc:
                raise PendingModel("embedding_model_unavailable", {"error": str(exc), "model": self.settings.embedding_model}) from exc
            result = embed_missing_retrieval_documents(
                index_store=index,
                embedding_store=embedding_store,
                embedder=embedder,
                model=self.settings.embedding_model,
                graph_scope="v2",
                session_id=str(job["session_id"]),
                extraction_run_id=str(job["job_id"]),
                limit=self.settings.auto_embedding_batch_size,
                embedding_kind=RETRIEVAL_EMBEDDING_KIND,
            )
        finally:
            conn.close()
        output = stage_dir / "embeddings_result.json"
        output.write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
        return StageResult(output_path=output, diagnostics=result.as_dict())

    def _stage_faiss(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        del job, artifact_dir
        conn = connect(self.settings.retrieval_db_path)
        try:
            embedding_store = GraphEmbeddingStore(conn, db_path=self.settings.retrieval_db_path)
            result = embedding_store.build_faiss_cache(
                embedding_kind=RETRIEVAL_EMBEDDING_KIND,
                model=self.settings.embedding_model,
                graph_scope="v2",
            )
        finally:
            conn.close()
        output = stage_dir / "faiss_result.json"
        output.write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
        return StageResult(output_path=output, diagnostics=result.as_dict())

    def _stage_quality_eval(self, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
        del job
        kuzu_result = _read_json(_stage_output(artifact_dir, "kuzu_write"))
        retrieval_result = _read_json(_stage_output(artifact_dir, "retrieval_docs"))
        output = stage_dir / "quality_eval.json"
        payload = {
            "ok": True,
            "kuzu": kuzu_result,
            "retrieval_docs": retrieval_result,
        }
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return StageResult(output_path=output, diagnostics=payload)


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


def require_complete_v2_reset_marker(marker: dict[str, Any] | None) -> dict[str, Any]:
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


def stage_config_hash(settings: Settings, *, stage: str) -> str:
    payload = {
        "stage": stage,
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "qwen_model": settings.qwen_model,
        "qwen_endpoint": settings.qwen_endpoint,
        "qwen_runtime": settings.qwen_runtime,
        "qwen_num_ctx": settings.qwen_num_ctx,
        "stage4_contract_hash": stage4_contract_hash(),
        "embedding_model": settings.embedding_model,
        "vector_backend": settings.vector_backend,
        "retrieval_max_doc_chars": settings.auto_retrieval_max_doc_chars,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


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
        "retrieval_docs": "retrieval_docs_result.json",
        "embeddings": "embeddings_result.json",
        "faiss": "faiss_result.json",
        "quality_eval": "quality_eval.json",
    }
    return artifact_dir / stage / candidates[stage]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        out.append({**item, "pipeline_version": PIPELINE_VERSION, "graph_schema_version": GRAPH_SCHEMA_VERSION, "session_id": job.get("session_id"), "job_id": job.get("job_id")})
    return out


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


def _upsert_compact_graph(store: GraphStore, nodes: tuple[dict[str, Any], ...], edges: tuple[dict[str, Any], ...], *, job: dict[str, Any]) -> None:
    namespace = str(job["job_id"]).rsplit(":", 1)[-1][:12]
    id_map = {str(node["id"]): f"{namespace}:{node['id']}" for node in nodes}
    for node in nodes:
        metadata = json.loads(str(node.get("properties_json") or "{}"))
        metadata.update(
            {
                "original_node_id": node.get("id"),
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "job_id": job.get("job_id"),
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
    for index, edge in enumerate(edges):
        source = id_map.get(str(edge.get("from_id") or ""))
        target = id_map.get(str(edge.get("to_id") or ""))
        if not source or not target:
            continue
        metadata = json.loads(str(edge.get("properties_json") or "{}"))
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
