from __future__ import annotations

from pathlib import Path
from typing import Any

from ....application.services.central_merge import CentralMergeService
from ..job_runner import StageFailed
from ..job_runner import StageResult
from ..job_runner import _product_manifest_info
from ..stage_artifacts import _read_json
from ..stage_artifacts import _stage_output


def run_central_version_merge_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    job_artifact_dir = Path(str(job["artifact_dir"]))
    session_graph_result = _read_json(_stage_output(job_artifact_dir, "kuzu_write"))
    manifest_info = _product_manifest_info(job_artifact_dir)
    manifest_path = Path(str(manifest_info["curated_manifest_path"]))
    compact_graph = _read_json(manifest_path)

    service = CentralMergeService(runner.settings, store=runner.job_store)
    result = service.plan_and_apply_session_graph(
        job=job,
        session_graph_result=session_graph_result if isinstance(session_graph_result, dict) else {},
        compact_graph=compact_graph if isinstance(compact_graph, dict) else {},
        manifest_info=manifest_info,
        manifest_path=manifest_path,
        stage_dir=stage_dir,
        graph_store_factory=runner.graph_store_factory,
        lock_owner=f"production-job:{job.get('job_id') or ''}",
    )
    apply_result = result.apply_result
    if not apply_result.get("ok"):
        raise StageFailed("central_merge_apply_failed", apply_result)
    return StageResult(output_path=result.output_path, diagnostics=result.diagnostics)

