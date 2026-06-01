from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ....domain.evidence import build_reasoning_evidence_view
from ....domain.evidence import git_commit_truth
from ....domain.evidence import write_reasoning_evidence_view_artifacts
from ....domain.reasoning import build_reasoning_work_packets_from_view
from ....domain.versioning import resolve_repo_identity
from ....domain.versioning import resolve_session_repo_root
from ..job_runner import StageFailed
from ..job_runner import StageResult
from ..job_runner import _first_transcript_path
from ..job_runner import _path_changed
from ..job_runner import _session_records
from ..stage_artifacts import _read_json
from ..stage_artifacts import _read_jsonl_records
from ..stage_artifacts import _write_jsonl


def run_evidence_view_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    session_jsonl = stage_dir / "session_raw_evidence.jsonl"
    records = _session_records(runner.settings.evidence_dir, str(job["session_id"]))
    if not records:
        raise RuntimeError(f"no_raw_evidence_for_session:{job['session_id']}")
    _write_jsonl(session_jsonl, records)
    transcript_path = _first_transcript_path(records)
    repo_resolution = resolve_session_repo_root(
        records,
        transcript_path=transcript_path,
        fallback_repo_path=str(job.get("repo_path") or ""),
    )
    repo_root = Path(str(repo_resolution.repo_root or job.get("repo_path") or ".")).resolve()
    repo_identity = resolve_repo_identity(repo_root)
    if repo_resolution.resolved and (
        _path_changed(str(job.get("repo_path") or ""), str(repo_root))
        or str(job.get("repo_id") or "") != repo_identity.repo_id
    ):
        runner.job_store.update_job_repo_identity(
            job_id=str(job["job_id"]),
            repo_path=str(repo_root),
            repo_id=repo_identity.repo_id,
            reason="session repo root resolved from evidence commits",
            metadata={"repo_resolution": repo_resolution.as_dict(), "repo_identity": repo_identity.as_dict()},
        )
        job["repo_path"] = str(repo_root)
        job["repo_id"] = repo_identity.repo_id
    build = build_reasoning_evidence_view(
        session_jsonl,
        transcript_path=Path(transcript_path) if transcript_path else None,
        repo_root=repo_root,
    )
    build.view["repo_resolution"] = repo_resolution.as_dict()
    write_reasoning_evidence_view_artifacts(build, stage_dir)
    output = stage_dir / "reasoning_evidence_view.json"
    return StageResult(
        output_path=output,
        diagnostics={"raw_record_count": len(records), "quality": build.quality, "repo_resolution": repo_resolution.as_dict()},
    )


def run_work_packets_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    view = _read_json(runner._stage_input_artifact(job, "work_packets", Path(str(job["artifact_dir"]))))
    repo_resolution: dict[str, Any] | None = None
    if isinstance(view, dict):
        repaired_view, repo_resolution = repair_unresolved_commit_truth(runner, job, view)
        view = repaired_view
    build = build_reasoning_work_packets_from_view(view)
    output = stage_dir / "reasoning_work_packets.json"
    output.write_text(json.dumps(list(build.packets), indent=2, ensure_ascii=False), encoding="utf-8")
    (stage_dir / "packet_quality_inventory.json").write_text(json.dumps(build.quality, indent=2, ensure_ascii=False), encoding="utf-8")
    (stage_dir / "quarantined_commits.json").write_text(json.dumps(list(build.quarantined_commits), indent=2), encoding="utf-8")
    if repo_resolution:
        (stage_dir / "repo_resolution.json").write_text(json.dumps(repo_resolution, indent=2, ensure_ascii=False), encoding="utf-8")
    if build.quality.get("stage_acceptance") != "PASS":
        reason = "no_commit_backed_work_packets" if not build.packets else "work_packets_acceptance_failed"
        raise StageFailed(
            reason,
            {
                "quality": build.quality,
                "packet_artifact": str(output),
                "quarantined_commit_count": len(build.quarantined_commits),
                "repo_resolution": repo_resolution or {},
                "note": "Production answer-grade graph output requires at least one resolved Git commit-backed work packet.",
            },
        )
    diagnostics = {"quality": build.quality}
    if repo_resolution:
        diagnostics["repo_resolution"] = repo_resolution
    return StageResult(output_path=output, diagnostics=diagnostics)


def repair_unresolved_commit_truth(
    runner: Any,
    job: dict[str, Any],
    view: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    commit_facts = [item for item in view.get("commit_facts", []) if isinstance(item, dict)]
    if not commit_facts:
        return view, None
    resolved_count = sum(
        1
        for item in commit_facts
        if isinstance(item.get("git_truth"), dict) and item["git_truth"].get("resolved") is True
    )
    if resolved_count:
        return view, None
    raw_path = Path(str(view.get("input_raw") or ""))
    if not raw_path.exists():
        return view, None
    records = _read_jsonl_records(raw_path)
    repo_resolution = resolve_session_repo_root(
        records,
        transcript_path=str(view.get("transcript_path") or ""),
        fallback_repo_path=str(job.get("repo_path") or ""),
    )
    if not repo_resolution.resolved:
        return view, repo_resolution.as_dict()
    repo_root = Path(repo_resolution.repo_root).resolve()
    repo_identity = resolve_repo_identity(repo_root)
    repaired = json.loads(json.dumps(view))
    repaired_commits: list[dict[str, Any]] = []
    for commit in commit_facts:
        next_commit = dict(commit)
        next_commit["git_truth"] = git_commit_truth(str(commit.get("commit_id") or ""), repo_root=repo_root)
        repaired_commits.append(next_commit)
    repaired["commit_facts"] = repaired_commits
    repaired["repo_resolution"] = repo_resolution.as_dict()
    repaired_count = sum(1 for item in repaired_commits if item.get("git_truth", {}).get("resolved") is True)
    resolution_payload = {**repo_resolution.as_dict(), "repaired_commit_truth_count": repaired_count}
    if repaired_count and (
        _path_changed(str(job.get("repo_path") or ""), str(repo_root))
        or str(job.get("repo_id") or "") != repo_identity.repo_id
    ):
        runner.job_store.update_job_repo_identity(
            job_id=str(job["job_id"]),
            repo_path=str(repo_root),
            repo_id=repo_identity.repo_id,
            reason="work packet repo root repaired from evidence commits",
            metadata={**resolution_payload, "repo_identity": repo_identity.as_dict()},
        )
        job["repo_path"] = str(repo_root)
        job["repo_id"] = repo_identity.repo_id
    return repaired, resolution_payload
