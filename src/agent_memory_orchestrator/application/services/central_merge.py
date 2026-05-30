from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...domain.versioning.identity import atoms_by_canonical_key
from ...domain.versioning.repo_identity import resolve_repo_identity
from ...graph.store import GraphStore
from ...reasoning_graph.central_merge.applier import apply_merge_plan
from ...reasoning_graph.central_merge.applier import repo_central_graph_path
from ...reasoning_graph.central_merge.planner import build_dry_run_merge_plan
from ...reasoning_graph.jobs import ProductionSessionJobStore

GraphStoreFactory = Callable[[Path], GraphStore]


@dataclass(slots=True, frozen=True)
class CentralMergeRunResult:
    output_path: Path
    diagnostics: dict[str, Any]
    apply_result: dict[str, Any]
    plan_payload: dict[str, Any]


__all__ = [
    "CentralMergeRunResult",
    "CentralMergeService",
    "apply_merge_plan",
    "build_dry_run_merge_plan",
    "repo_central_graph_path",
]


class CentralMergeService:
    """Application boundary for central graph planning and apply operations."""

    def __init__(self, settings: Settings, *, store: ProductionSessionJobStore | None = None) -> None:
        self.settings = settings
        self.store = store

    def central_graph_path(self, repo_id: str) -> Path:
        return repo_central_graph_path(self.settings, repo_id)

    def plan_and_apply_session_graph(
        self,
        *,
        job: dict[str, Any],
        session_graph_result: dict[str, Any],
        compact_graph: dict[str, Any],
        manifest_info: dict[str, Any],
        manifest_path: Path,
        stage_dir: Path,
        graph_store_factory: GraphStoreFactory,
        lock_owner: str,
        branch: str = "main",
        mode: str = "active",
    ) -> CentralMergeRunResult:
        if self.store is None:
            raise ValueError("CentralMergeService.plan_and_apply_session_graph requires a job store")

        repo_id = str(job.get("repo_id") or "") or resolve_repo_identity(str(job.get("repo_path") or "")).repo_id
        active_view = self.store.ensure_graph_view(repo_id=repo_id, branch=branch, mode=mode)
        parent_graph_commit_id = str(active_view.get("graph_commit_id") or "")
        existing_atom_scan_error = ""
        try:
            existing_atoms = self._central_atoms_by_canonical_key(repo_id=repo_id, graph_store_factory=graph_store_factory)
            active_central_versions = self._central_active_versions(repo_id=repo_id, graph_store_factory=graph_store_factory)
        except Exception as exc:
            existing_atoms = {}
            active_central_versions = []
            existing_atom_scan_error = f"{type(exc).__name__}: {exc}"
        historical_decision_frames = self.store.list_decision_frames(
            repo_id=repo_id,
            exclude_job_id=str(job.get("job_id") or ""),
        )
        plan = build_dry_run_merge_plan(
            job={**job, "repo_id": repo_id},
            compact_graph=compact_graph,
            parent_graph_commit_id=parent_graph_commit_id,
            existing_atoms_by_canonical_key=existing_atoms,
            active_central_versions=active_central_versions,
            historical_decision_frames=historical_decision_frames,
        )
        plan_payload = plan.as_dict()
        plan_payload["session_graph_write"] = session_graph_result
        plan_payload["input_source"] = "curated_graph_manifest"
        plan_payload["curated_input_hash"] = manifest_info["curated_input_hash"]
        plan_payload["trace_input_hash"] = manifest_info["trace_input_hash"]
        plan_payload["curated_manifest_path"] = str(manifest_path)
        plan_payload["apply_scope"] = plan.metrics.get("apply_scope", [])
        plan_payload["deferred_atom_kinds"] = ["symbol", "code_region", "decision", "problem"]
        plan_payload["deferred_atom_counts"] = plan.metrics.get("deferred_atom_counts", {})
        if existing_atom_scan_error:
            plan_payload["existing_atom_scan_error"] = existing_atom_scan_error

        self.store.upsert_central_merge_plan(plan_payload)
        output = stage_dir / "merge_plan.json"
        output.write_text(json.dumps(plan_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        central_graph = graph_store_factory(repo_central_graph_path(self.settings, repo_id))
        try:
            apply_result = self.apply_plan(
                plan_id=plan.plan_id,
                graph_store=central_graph,
                branch=branch,
                mode=mode,
                lock_owner=lock_owner,
            )
        finally:
            central_graph.close()

        diagnostics = {
            "status": apply_result.get("status") or "applied",
            "mode": apply_result.get("mode") or "apply_exact_atoms",
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "input_graph_hash": plan.input_graph_hash,
            "repo_id": plan.repo_id,
            "repo_path": plan.repo_path,
            "graph_commit_id": apply_result.get("graph_commit_id") or "",
            "graph_view_id": apply_result.get("graph_view_id") or "",
            "result_artifact": apply_result.get("result_artifact") or "",
            "added_node_count": apply_result.get("added_node_count") or 0,
            "added_edge_count": apply_result.get("added_edge_count") or 0,
            "applied_atom_counts": apply_result.get("applied_atom_counts") or {},
            "applied_version_counts": apply_result.get("applied_version_counts") or {},
            "deferred_atom_counts": apply_result.get("deferred_atom_counts") or {},
            "status_update_count": apply_result.get("status_update_count") or 0,
            "graph_commit_preview": plan.graph_commit_preview,
            "metrics": plan.metrics,
            "review_candidate_count": len(plan.review_candidates),
            "existing_atom_scan_error": existing_atom_scan_error,
            "input_source": "curated_graph_manifest",
            "curated_input_hash": manifest_info["curated_input_hash"],
            "trace_input_hash": manifest_info["trace_input_hash"],
        }
        return CentralMergeRunResult(
            output_path=output,
            diagnostics=diagnostics,
            apply_result=apply_result,
            plan_payload=plan_payload,
        )

    def apply_plan(
        self,
        *,
        plan_id: str,
        graph_store: GraphStore | None = None,
        branch: str = "main",
        mode: str = "active",
        lock_owner: str | None = None,
    ) -> dict[str, Any]:
        return apply_merge_plan(
            settings=self.settings,
            plan_id=plan_id,
            store=self.store,
            graph_store=graph_store,
            branch=branch,
            mode=mode,
            lock_owner=lock_owner,
        )

    def _central_atoms_by_canonical_key(self, *, repo_id: str, graph_store_factory: GraphStoreFactory) -> dict[str, dict[str, Any]]:
        graph = graph_store_factory(repo_central_graph_path(self.settings, repo_id))
        try:
            graph.init_schema()
            nodes = graph.list_nodes(limit=1_000_000, kinds=["KnowledgeAtom"])
        finally:
            graph.close()
        return atoms_by_canonical_key(nodes)

    def _central_active_versions(self, *, repo_id: str, graph_store_factory: GraphStoreFactory) -> list[dict[str, Any]]:
        graph = graph_store_factory(repo_central_graph_path(self.settings, repo_id))
        try:
            graph.init_schema()
            nodes = graph.list_nodes(limit=1_000_000, kinds=["KnowledgeVersion"])
        finally:
            graph.close()
        out: list[dict[str, Any]] = []
        for node in nodes:
            metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            atom_kind = str(metadata.get("atom_kind") or "").lower()
            status = str(node.get("status") or metadata.get("status") or "")
            if str(metadata.get("repo_id") or "") == repo_id and (status in {"", "active"} or (atom_kind in {"decision", "problem"} and status == "review")):
                out.append(node)
        return out
