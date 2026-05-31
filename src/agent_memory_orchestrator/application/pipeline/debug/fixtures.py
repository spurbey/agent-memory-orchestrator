from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....core.db import connect
from ....infrastructure.faiss.embedding_store import GraphEmbeddingStore
from ....reasoning_graph.retrieval import RetrievalIndexStore
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore


def export_job_fixture(settings: Settings, *, job_id: str, out_dir: Path | None = None, copy_artifacts: bool = False) -> dict[str, Any]:
    store = ProductionSessionJobStore(settings)
    try:
        job = store.get_job(job_id)
        if job is None:
            raise ValueError(f"unknown_job:{job_id}")
        stages = store.list_stages(job_id)
        events = store.list_events(job_id, limit=500)
        marker = store.marker()
        central_plan = store.get_central_merge_plan_for_job(job_id)
        central_plan_id = str((central_plan or {}).get("plan_id") or "")
        central_repo_id = str((central_plan or {}).get("repo_id") or job.get("repo_id") or "")
        central_review_candidates = store.list_review_candidates(plan_id=central_plan_id) if central_plan_id else []
        central_graph_commit = store.get_graph_commit_for_plan(central_plan_id) if central_plan_id else None
        central_graph_view = store.graph_view(repo_id=central_repo_id, branch="main", mode="active")
    finally:
        store.close()

    target = out_dir or (settings.export_dir / "production-fixtures" / safe_name(job_id))
    target.mkdir(parents=True, exist_ok=True)
    artifact_inventory = _artifact_inventory(stages)
    if copy_artifacts:
        _copy_artifacts(artifact_inventory, target / "artifacts")
    retrieval = _retrieval_inventory(settings)
    semantic_context = _semantic_context(
        stages,
        retrieval,
        central_plan=central_plan,
        central_graph_commit=central_graph_commit,
        central_graph_view=central_graph_view,
        central_review_candidates=central_review_candidates,
    )
    payload = {
        "ok": True,
        "fixture_version": "production-semantic-fixture-v1",
        "job": job,
        "stages": stages,
        "events": events,
        "reset_marker": marker,
        "artifact_inventory": artifact_inventory,
        "kuzu_inventory": _kuzu_inventory(settings.graph_path),
        "retrieval": retrieval,
        "embedding_coverage": retrieval.get("embedding_coverage", {}),
        "faiss": _faiss_inventory(settings),
        "central_merge": {
            "plan": central_plan,
            "graph_commit": central_graph_commit,
            "graph_view": central_graph_view,
            "review_candidates": central_review_candidates,
        },
        "semantic_context": semantic_context,
    }
    fixture_path = target / "fixture.json"
    fixture_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(fixture_path), "fixture": payload}


def _artifact_inventory(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage in stages:
        for key in ("input_artifact", "output_artifact"):
            raw = str(stage.get(key) or "")
            if not raw:
                continue
            path = Path(raw)
            rows.append(
                {
                    "stage": stage.get("stage"),
                    "role": key.removesuffix("_artifact"),
                    "path": raw,
                    "exists": path.exists(),
                    "is_dir": path.is_dir() if path.exists() else False,
                    "hash": _path_hash(path),
                }
            )
            if stage.get("stage") == "central_version_merge" and key == "output_artifact" and path.name == "merge_plan.json":
                sidecar = path.parent / "merge_result.json"
                rows.append(
                    {
                        "stage": stage.get("stage"),
                        "role": "sidecar",
                        "path": str(sidecar),
                        "exists": sidecar.exists(),
                        "is_dir": sidecar.is_dir() if sidecar.exists() else False,
                        "hash": _path_hash(sidecar),
                    }
                )
    return rows


def _semantic_context(
    stages: list[dict[str, Any]],
    retrieval: dict[str, Any],
    *,
    central_plan: dict[str, Any] | None = None,
    central_graph_commit: dict[str, Any] | None = None,
    central_graph_view: dict[str, Any] | None = None,
    central_review_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_stage = {str(stage.get("stage") or ""): stage for stage in stages}
    artifacts = {name: _load_stage_json(row) for name, row in by_stage.items()}
    work_packets = artifacts.get("work_packets") if isinstance(artifacts.get("work_packets"), list) else []
    qwen_results = artifacts.get("qwen_reasoning") if isinstance(artifacts.get("qwen_reasoning"), list) else []
    reasoning_nodes = artifacts.get("reasoning_review") if isinstance(artifacts.get("reasoning_review"), list) else []
    merge_plan = artifacts.get("central_version_merge") if isinstance(artifacts.get("central_version_merge"), dict) else {}
    merge_result = _load_central_merge_result(by_stage.get("central_version_merge", {}))
    return {
        "stage_status": {name: str(row.get("status") or "") for name, row in by_stage.items()},
        "completed_stages": [name for name, row in by_stage.items() if row.get("status") == "complete"],
        "work_packets": {
            "count": len(work_packets),
            "packet_ids": [str(packet.get("packet_id") or "") for packet in work_packets if isinstance(packet, dict)],
            "commit_shas": _packet_commit_shas(work_packets),
        },
        "qwen_reasoning": {"result_count": len(qwen_results)},
        "reasoning_review": {"accepted_count": len(reasoning_nodes)},
        "session_graph_write": _session_graph_summary(artifacts.get("kuzu_write")),
        "central_version_merge": _merge_plan_summary(
            merge_plan,
            plan_row=central_plan,
            merge_result=merge_result,
            graph_commit=central_graph_commit,
            graph_view=central_graph_view,
            review_candidates=central_review_candidates or [],
        ),
        "retrieval": {
            "doc_count": retrieval.get("doc_count", 0),
            "embedding_coverage": retrieval.get("embedding_coverage", {}),
        },
    }


def _load_stage_json(stage: dict[str, Any]) -> Any:
    path = Path(str(stage.get("output_artifact") or ""))
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_central_merge_result(stage: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(stage.get("output_artifact") or ""))
    if not path.exists() or not path.is_file():
        return {}
    result_path = path.parent / "merge_result.json"
    if not result_path.exists() or not result_path.is_file():
        return {}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _packet_commit_shas(work_packets: list[Any]) -> list[str]:
    shas: list[str] = []
    for packet in work_packets:
        if not isinstance(packet, dict):
            continue
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        sha = str(commit.get("full_sha") or commit.get("short_sha") or "")
        if sha and sha not in shas:
            shas.append(sha)
    return shas


def _session_graph_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"available": False}
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    return {
        "available": True,
        "ok": payload.get("ok") is True,
        "node_count": inventory.get("node_count", inventory.get("nodes", 0)),
        "edge_count": inventory.get("edge_count", inventory.get("edges", 0)),
        "unresolved_edge_count": inventory.get("unresolved_edge_count", 0),
    }


def _merge_plan_summary(
    plan: dict[str, Any],
    *,
    plan_row: dict[str, Any] | None = None,
    merge_result: dict[str, Any] | None = None,
    graph_commit: dict[str, Any] | None = None,
    graph_view: dict[str, Any] | None = None,
    review_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row_plan = (plan_row or {}).get("plan") if isinstance((plan_row or {}).get("plan"), dict) else {}
    effective_plan = plan or row_plan
    if not effective_plan and not plan_row:
        return {"available": False}
    result = merge_result or {}
    commit = graph_commit or {}
    view = graph_view or {}
    status = str((plan_row or {}).get("status") or result.get("status") or effective_plan.get("status") or "")
    mode = str((plan_row or {}).get("mode") or result.get("mode") or effective_plan.get("mode") or "")
    diagnostics = (plan_row or {}).get("diagnostics") if isinstance((plan_row or {}).get("diagnostics"), dict) else {}
    graph_commit_payload = result.get("graph_commit") if isinstance(result.get("graph_commit"), dict) else {}
    graph_view_payload = result.get("graph_view") if isinstance(result.get("graph_view"), dict) else {}
    graph_commit_id = str(
        (commit or {}).get("graph_commit_id")
        or graph_commit_payload.get("graph_commit_id")
        or diagnostics.get("graph_commit_id")
        or (effective_plan.get("graph_commit_preview") if isinstance(effective_plan.get("graph_commit_preview"), dict) else {}).get("graph_commit_id")
        or ""
    )
    atoms = effective_plan.get("new_atoms") if isinstance(effective_plan.get("new_atoms"), list) else []
    matched_atoms = effective_plan.get("matched_atoms") if isinstance(effective_plan.get("matched_atoms"), list) else []
    versions = effective_plan.get("new_versions") if isinstance(effective_plan.get("new_versions"), list) else []
    plan_review_candidates = effective_plan.get("review_candidates") if isinstance(effective_plan.get("review_candidates"), list) else []
    persisted_review_candidates = review_candidates or []
    return {
        "available": True,
        "plan_id": (plan_row or {}).get("plan_id", effective_plan.get("plan_id", "")),
        "plan_hash": (plan_row or {}).get("plan_hash", effective_plan.get("plan_hash", "")),
        "mode": mode,
        "status": status,
        "applied": status == "applied" or result.get("status") == "applied" or commit.get("status") == "applied",
        "repo_id": (plan_row or {}).get("repo_id", effective_plan.get("repo_id", "")),
        "graph_commit_id": graph_commit_id,
        "active_graph_view_id": str((view or {}).get("view_id") or graph_view_payload.get("view_id") or ""),
        "active_graph_view_head": str((view or {}).get("graph_commit_id") or graph_view_payload.get("graph_commit_id") or ""),
        "graph_commit_status": str((commit or {}).get("status") or graph_commit_payload.get("status") or ""),
        "graph_commit_preview": effective_plan.get("graph_commit_preview", {}),
        "merge_result_available": bool(result),
        "merge_result_artifact": str(result.get("result_artifact") or ""),
        "added_node_count": int(result.get("added_node_count") or (commit.get("added_nodes") and len(commit.get("added_nodes"))) or 0),
        "added_edge_count": int(result.get("added_edge_count") or (commit.get("added_edges") and len(commit.get("added_edges"))) or 0),
        "new_atom_count": len(atoms),
        "matched_atom_count": len(matched_atoms),
        "new_version_count": len(versions),
        "review_candidate_count": max(len(plan_review_candidates), len(persisted_review_candidates)),
        "atom_kinds": sorted({str(atom.get("atom_kind") or "") for atom in atoms if isinstance(atom, dict)}),
        "version_source_complete": all(bool(version.get("source_node_ids")) for version in versions if isinstance(version, dict)),
        "raw_plan": effective_plan,
        "raw_result": result,
    }


def _copy_artifacts(inventory: list[dict[str, Any]], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in inventory:
        if item.get("role") not in {"output", "sidecar"} or not item.get("exists"):
            continue
        source = Path(str(item.get("path") or ""))
        dest = target / safe_name(str(item.get("stage") or "stage"))
        if source.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / source.name)


def _retrieval_inventory(settings: Settings) -> dict[str, Any]:
    if not settings.retrieval_db_path.exists():
        return {"exists": False, "doc_count": 0, "embedding_coverage": {"status": "missing"}}
    conn = connect(settings.retrieval_db_path)
    try:
        index = RetrievalIndexStore(conn)
        docs = index.list_documents(limit=1_000_000)
        doc_count = len(docs)
        doc_ids = {doc.doc_id for doc in docs}
        embedding_store = GraphEmbeddingStore(conn, db_path=settings.retrieval_db_path)
        embeddings = embedding_store.list_records()
        embedded_docs = len({record.graph_path for record in embeddings if record.graph_path in doc_ids})
    finally:
        conn.close()
    coverage = (embedded_docs / doc_count) if doc_count else 0.0
    status = "ready" if doc_count and embedded_docs >= doc_count else "partial" if embedded_docs else "missing"
    return {
        "exists": True,
        "path": str(settings.retrieval_db_path),
        "doc_count": doc_count,
        "embedding_count": embedded_docs,
        "embedding_coverage": {
            "total_docs": doc_count,
            "embedded_docs": embedded_docs,
            "coverage_percent": round(coverage * 100, 3),
            "status": status,
        },
    }


def _kuzu_inventory(graph_path: Path) -> dict[str, Any]:
    return {
        "path": str(graph_path),
        "exists": graph_path.exists(),
        "non_empty": any(graph_path.iterdir()) if graph_path.exists() and graph_path.is_dir() else graph_path.exists(),
        "hash": _path_hash(graph_path),
    }


def _faiss_inventory(settings: Settings) -> dict[str, Any]:
    root = settings.retrieval_db_path.parent / "indexes" / settings.retrieval_db_path.stem
    files = list(root.rglob("*")) if root.exists() else []
    return {"path": str(root), "exists": root.exists(), "file_count": sum(1 for path in files if path.is_file()), "hash": _path_hash(root)}


def _path_hash(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_file():
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            return f"unavailable:{type(exc).__name__}"
    rows = []
    try:
        children = sorted(path.rglob("*"))
    except OSError as exc:
        return f"unavailable:{type(exc).__name__}"
    for child in children:
        if not child.is_file():
            continue
        try:
            digest = hashlib.sha256(child.read_bytes()).hexdigest()
        except OSError as exc:
            digest = f"unavailable:{type(exc).__name__}"
        rows.append(f"{child.relative_to(path)}:{digest}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)[:120] or "item"


