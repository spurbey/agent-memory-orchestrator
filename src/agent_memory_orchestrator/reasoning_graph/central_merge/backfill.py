from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...graph.store import KuzuGraphStore
from ..jobs.store import V2SessionJobStore
from ..jobs.store import utc_now
from ...domain.versioning.identity import atoms_by_canonical_key
from ...domain.versioning.repo_identity import resolve_repo_identity
from .planner import build_dry_run_merge_plan


def backfill_central_merge_plan(settings: Settings, *, job_id: str, forced_by: str = "manual-backfill") -> dict[str, Any]:
    store = V2SessionJobStore(settings)
    try:
        job = store.get_job(job_id)
        if job is None:
            raise ValueError(f"unknown_job:{job_id}")
        artifact_dir = Path(str(job.get("artifact_dir") or ""))
        kuzu_result_path = artifact_dir / "kuzu_write" / "kuzu_write_result.json"
        manifest_path = artifact_dir / "kuzu_write" / "compact_graph_manifest.json"
        if not kuzu_result_path.exists():
            raise FileNotFoundError(f"missing_kuzu_write_result:{kuzu_result_path}")
        if not manifest_path.exists():
            raise FileNotFoundError(f"missing_compact_graph_manifest:{manifest_path}")
        kuzu_result = _read_json(kuzu_result_path)
        compact_graph = _read_json(manifest_path)
        repo_id = str(job.get("repo_id") or "") or resolve_repo_identity(str(job.get("repo_path") or "")).repo_id
        active_view = store.ensure_graph_view(repo_id=repo_id, branch="main", mode="active")
        existing_atoms = _central_atoms_by_canonical_key(settings)
        plan = build_dry_run_merge_plan(
            job=job,
            compact_graph=compact_graph if isinstance(compact_graph, dict) else {},
            parent_graph_commit_id=str(active_view.get("graph_commit_id") or ""),
            existing_atoms_by_canonical_key=existing_atoms,
        )
        stage_dir = artifact_dir / "central_version_merge"
        stage_dir.mkdir(parents=True, exist_ok=True)
        plan_payload = plan.as_dict()
        plan_payload["session_graph_write"] = kuzu_result if isinstance(kuzu_result, dict) else {}
        output = stage_dir / "merge_plan.json"
        output.write_text(json.dumps(plan_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        store.upsert_central_merge_plan(plan_payload)
        _upsert_backfill_stage(
            store,
            job_id=job_id,
            input_artifact=str(kuzu_result_path),
            output_artifact=str(output),
            diagnostics={
                "backfilled": True,
                "forced_by": forced_by,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "repo_id": plan.repo_id,
                "metrics": plan.metrics,
            },
        )
        store.log_event(
            job_id=job_id,
            event_type="central_merge_backfilled",
            stage="central_version_merge",
            message="central_version_merge dry-run plan backfilled for existing job",
            metadata={"forced_by": forced_by, "plan_id": plan.plan_id},
        )
        return {"ok": True, "job_id": job_id, "plan": store.get_central_merge_plan(plan.plan_id), "output_artifact": str(output)}
    finally:
        store.close()


def _upsert_backfill_stage(
    store: V2SessionJobStore,
    *,
    job_id: str,
    input_artifact: str,
    output_artifact: str,
    diagnostics: dict[str, Any],
) -> None:
    now = utc_now()
    store.conn.execute(
        """
        INSERT INTO v2_session_job_stages(
          job_id, stage, status, input_artifact, output_artifact,
          input_hash, output_hash, stage_config_hash, diagnostics_json,
          started_at, finished_at
        )
        VALUES(?, 'central_version_merge', 'complete', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id, stage) DO UPDATE SET
          status='complete',
          input_artifact=excluded.input_artifact,
          output_artifact=excluded.output_artifact,
          input_hash=excluded.input_hash,
          output_hash=excluded.output_hash,
          stage_config_hash=excluded.stage_config_hash,
          diagnostics_json=excluded.diagnostics_json,
          finished_at=excluded.finished_at
        """,
        (
            job_id,
            input_artifact,
            output_artifact,
            _file_hash(Path(input_artifact)),
            _file_hash(Path(output_artifact)),
            "central_version_merge_backfill_v1",
            json.dumps(diagnostics, sort_keys=True),
            now,
            now,
        ),
    )
    store.conn.commit()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _central_atoms_by_canonical_key(settings: Settings) -> dict[str, dict[str, Any]]:
    graph = KuzuGraphStore(settings.graph_path)
    try:
        graph.init_schema()
        nodes = graph.list_nodes(limit=1_000_000, kinds=["KnowledgeAtom"])
    finally:
        graph.close()
    return atoms_by_canonical_key(nodes)


def _file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()
