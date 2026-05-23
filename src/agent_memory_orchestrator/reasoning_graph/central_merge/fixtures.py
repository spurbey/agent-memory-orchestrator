from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...core.db import connect
from ..embedding_store import GraphEmbeddingStore
from ..retrieval import RetrievalIndexStore
from ..jobs.store import V2SessionJobStore


def export_job_fixture(settings: Settings, *, job_id: str, out_dir: Path | None = None, copy_artifacts: bool = False) -> dict[str, Any]:
    store = V2SessionJobStore(settings)
    try:
        job = store.get_job(job_id)
        if job is None:
            raise ValueError(f"unknown_job:{job_id}")
        stages = store.list_stages(job_id)
        events = store.list_events(job_id, limit=500)
        marker = store.marker()
    finally:
        store.close()

    target = out_dir or (settings.export_dir / "v2-fixtures" / safe_name(job_id))
    target.mkdir(parents=True, exist_ok=True)
    artifact_inventory = _artifact_inventory(stages)
    if copy_artifacts:
        _copy_artifacts(artifact_inventory, target / "artifacts")
    retrieval = _retrieval_inventory(settings)
    semantic_context = _semantic_context(stages, retrieval)
    payload = {
        "ok": True,
        "fixture_version": "v2-semantic-fixture-v1",
        "job": job,
        "stages": stages,
        "events": events,
        "reset_marker": marker,
        "artifact_inventory": artifact_inventory,
        "kuzu_inventory": _kuzu_inventory(settings.graph_path),
        "retrieval": retrieval,
        "embedding_coverage": retrieval.get("embedding_coverage", {}),
        "faiss": _faiss_inventory(settings),
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
    return rows


def _semantic_context(stages: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    by_stage = {str(stage.get("stage") or ""): stage for stage in stages}
    artifacts = {name: _load_stage_json(row) for name, row in by_stage.items()}
    work_packets = artifacts.get("work_packets") if isinstance(artifacts.get("work_packets"), list) else []
    qwen_results = artifacts.get("qwen_reasoning") if isinstance(artifacts.get("qwen_reasoning"), list) else []
    reasoning_nodes = artifacts.get("reasoning_review") if isinstance(artifacts.get("reasoning_review"), list) else []
    merge_plan = artifacts.get("central_version_merge") if isinstance(artifacts.get("central_version_merge"), dict) else {}
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
        "central_version_merge": _merge_plan_summary(merge_plan),
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


def _merge_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    if not plan:
        return {"available": False}
    atoms = plan.get("new_atoms") if isinstance(plan.get("new_atoms"), list) else []
    versions = plan.get("new_versions") if isinstance(plan.get("new_versions"), list) else []
    review_candidates = plan.get("review_candidates") if isinstance(plan.get("review_candidates"), list) else []
    return {
        "available": True,
        "plan_id": plan.get("plan_id", ""),
        "plan_hash": plan.get("plan_hash", ""),
        "mode": plan.get("mode", ""),
        "status": plan.get("status", ""),
        "repo_id": plan.get("repo_id", ""),
        "graph_commit_preview": plan.get("graph_commit_preview", {}),
        "new_atom_count": len(atoms),
        "new_version_count": len(versions),
        "review_candidate_count": len(review_candidates),
        "atom_kinds": sorted({str(atom.get("atom_kind") or "") for atom in atoms if isinstance(atom, dict)}),
        "version_source_complete": all(bool(version.get("source_node_ids")) for version in versions if isinstance(version, dict)),
        "raw_plan": plan,
    }


def _copy_artifacts(inventory: list[dict[str, Any]], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in inventory:
        if item.get("role") != "output" or not item.get("exists"):
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
        doc_count = len(index.list_documents(limit=1_000_000))
        embedding_store = GraphEmbeddingStore(conn, db_path=settings.retrieval_db_path)
        embeddings = embedding_store.list_records()
        embedded_docs = len({record.node_id for record in embeddings})
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
        return hashlib.sha256(path.read_bytes()).hexdigest()
    rows = []
    for child in sorted(path.rglob("*")):
        if child.is_file():
            rows.append(f"{child.relative_to(path)}:{hashlib.sha256(child.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)[:120] or "item"
