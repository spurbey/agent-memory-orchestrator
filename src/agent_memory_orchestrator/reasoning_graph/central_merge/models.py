from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import Any


CENTRAL_MERGE_PLAN_VERSION = "central-version-merge-dryrun-v1"
CANONICAL_KEY_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def merge_plan_id_for(*, job_id: str, session_id: str, repo_id: str, parent_graph_commit_id: str, input_graph_hash: str) -> str:
    seed = {
        "job_id": job_id,
        "session_id": session_id,
        "repo_id": repo_id,
        "parent_graph_commit_id": parent_graph_commit_id,
        "input_graph_hash": input_graph_hash,
        "plan_version": CENTRAL_MERGE_PLAN_VERSION,
    }
    return f"v2plan:{stable_hash(seed)[:32]}"


@dataclass(slots=True, frozen=True)
class KnowledgeAtomPreview:
    atom_id: str
    atom_kind: str
    repo_id: str
    repo_path: str
    canonical_key: str
    canonical_key_version: int = CANONICAL_KEY_VERSION
    source_node_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class KnowledgeVersionPreview:
    version_id: str
    atom_id: str
    atom_kind: str
    status: str
    job_id: str
    session_id: str
    source_node_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ReviewCandidate:
    candidate_id: str
    plan_id: str
    job_id: str
    source_node_id: str
    target_node_id: str
    proposed_relation: str
    score: dict[str, Any]
    reason: str
    status: str = "open"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MergePlan:
    plan_id: str
    job_id: str
    session_id: str
    repo_id: str
    repo_path: str
    status: str
    mode: str
    parent_graph_commit_id: str
    input_graph_hash: str
    plan_hash: str
    plan_version: str = CENTRAL_MERGE_PLAN_VERSION
    pipeline_version: str = ""
    graph_schema_version: str = ""
    new_atoms: list[dict[str, Any]] = field(default_factory=list)
    matched_atoms: list[dict[str, Any]] = field(default_factory=list)
    new_versions: list[dict[str, Any]] = field(default_factory=list)
    version_edges: list[dict[str, Any]] = field(default_factory=list)
    review_candidates: list[dict[str, Any]] = field(default_factory=list)
    unresolved_identity: list[dict[str, Any]] = field(default_factory=list)
    graph_commit_preview: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        job_id: str,
        session_id: str,
        repo_id: str,
        repo_path: str,
        parent_graph_commit_id: str,
        input_graph_hash: str,
        pipeline_version: str,
        graph_schema_version: str,
        new_atoms: list[dict[str, Any]] | None = None,
        matched_atoms: list[dict[str, Any]] | None = None,
        new_versions: list[dict[str, Any]] | None = None,
        version_edges: list[dict[str, Any]] | None = None,
        review_candidates: list[dict[str, Any]] | None = None,
        unresolved_identity: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> "MergePlan":
        plan_id = merge_plan_id_for(
            job_id=job_id,
            session_id=session_id,
            repo_id=repo_id,
            parent_graph_commit_id=parent_graph_commit_id,
            input_graph_hash=input_graph_hash,
        )
        graph_commit_preview = {
            "graph_commit_id": f"v2gcommit:{stable_hash({'plan_id': plan_id, 'input_graph_hash': input_graph_hash})[:32]}",
            "status": "preview",
            "parent_graph_commit_id": parent_graph_commit_id,
            "merge_plan_id": plan_id,
            "pipeline_version": pipeline_version,
            "graph_schema_version": graph_schema_version,
        }
        body = {
            "plan_id": plan_id,
            "plan_version": CENTRAL_MERGE_PLAN_VERSION,
            "job_id": job_id,
            "session_id": session_id,
            "repo_id": repo_id,
            "repo_path": repo_path,
            "parent_graph_commit_id": parent_graph_commit_id,
            "input_graph_hash": input_graph_hash,
            "pipeline_version": pipeline_version,
            "graph_schema_version": graph_schema_version,
            "new_atoms": new_atoms or [],
            "matched_atoms": matched_atoms or [],
            "new_versions": new_versions or [],
            "version_edges": version_edges or [],
            "review_candidates": review_candidates or [],
            "unresolved_identity": unresolved_identity or [],
            "graph_commit_preview": graph_commit_preview,
            "metrics": metrics or {},
            "diagnostics": diagnostics or {},
        }
        return cls(
            plan_id=plan_id,
            job_id=job_id,
            session_id=session_id,
            repo_id=repo_id,
            repo_path=repo_path,
            status="planned",
            mode="dry_run",
            parent_graph_commit_id=parent_graph_commit_id,
            input_graph_hash=input_graph_hash,
            plan_hash=stable_hash(body),
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
            new_atoms=new_atoms or [],
            matched_atoms=matched_atoms or [],
            new_versions=new_versions or [],
            version_edges=version_edges or [],
            review_candidates=review_candidates or [],
            unresolved_identity=unresolved_identity or [],
            graph_commit_preview=graph_commit_preview,
            metrics=metrics or {},
            diagnostics=diagnostics or {},
        )
