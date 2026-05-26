from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..jobs.constants import GRAPH_SCHEMA_VERSION
from ..jobs.constants import PIPELINE_VERSION
from .decision import build_decision_review_candidates
from .models import CANONICAL_KEY_VERSION
from .models import KnowledgeAtomPreview
from .models import KnowledgeVersionPreview
from .models import MergePlan
from .models import merge_plan_id_for
from .models import stable_hash
from .repo_identity import RepoIdentity
from .repo_identity import resolve_repo_identity


EXACT_ATOM_KINDS = frozenset({"commit", "file", "symbol", "code_region"})
SAFE_APPLY_ATOM_KINDS = frozenset({"commit", "file"})


def build_dry_run_merge_plan(
    *,
    job: dict[str, Any],
    compact_graph: dict[str, Any],
    parent_graph_commit_id: str = "",
    existing_atoms_by_canonical_key: dict[str, dict[str, Any]] | None = None,
    active_central_versions: list[dict[str, Any]] | None = None,
    historical_decision_frames: list[dict[str, Any]] | None = None,
) -> MergePlan:
    repo = _repo_identity_from_job(job)
    nodes = _nodes(compact_graph)
    input_graph_hash = stable_hash({"nodes": nodes, "edges": compact_graph.get("edges", [])})
    job_id = str(job["job_id"])
    session_id = str(job["session_id"])
    plan_id = merge_plan_id_for(
        job_id=job_id,
        session_id=session_id,
        repo_id=repo.repo_id,
        parent_graph_commit_id=parent_graph_commit_id,
        input_graph_hash=input_graph_hash,
    )
    atoms, versions, unresolved = _exact_atom_previews(
        nodes=nodes,
        repo_id=repo.repo_id,
        repo_path=repo.repo_path,
        job_id=job_id,
        session_id=session_id,
    )
    new_atoms, matched_atoms, remapped_versions = _split_new_and_matched_atoms(
        atoms=[atom.as_dict() for atom in atoms],
        versions=[version.as_dict() for version in versions],
        existing_atoms_by_canonical_key=existing_atoms_by_canonical_key or {},
    )
    decision_result = build_decision_review_candidates(
        compact_graph=compact_graph,
        central_nodes=active_central_versions or [],
        historical_frames=historical_decision_frames or [],
        repo_id=repo.repo_id,
        job_id=job_id,
        plan_id=plan_id,
    )
    review_candidates = decision_result.get("candidates") if isinstance(decision_result.get("candidates"), list) else []
    decision_metrics = decision_result.get("metrics") if isinstance(decision_result.get("metrics"), dict) else {}
    decision_frames = decision_result.get("frames") if isinstance(decision_result.get("frames"), list) else []
    metrics = {
        "mode": "dry_run",
        "node_count": len(nodes),
        "edge_count": len(compact_graph.get("edges", []) if isinstance(compact_graph.get("edges"), list) else []),
        "exact_atom_created_count": len(new_atoms),
        "exact_atom_matched_count": len(matched_atoms),
        "new_version_count": len(remapped_versions),
        "apply_scope": ["commit", "file", "knowledge_version", "graph_commit", "graph_view"],
        "deferred_atom_counts": _deferred_atom_counts(new_atoms + matched_atoms),
        "unresolved_identity_count": len(unresolved),
        "repo_id_resolution_status": repo.source,
        "review_candidate_count": len(review_candidates),
        **decision_metrics,
    }
    diagnostics = {
        "repo_identity": repo.as_dict(),
        "decision_frames": decision_frames,
        "apply_scope": ["commit", "file", "knowledge_version", "graph_commit", "graph_view"],
        "deferred_atom_kinds": ["symbol", "code_region", "decision", "problem"],
        "deferred_atom_counts": _deferred_atom_counts(new_atoms + matched_atoms),
        "note": "Dry-run only. No central Kuzu mutation is performed by this stage.",
    }
    return MergePlan.build(
        job_id=job_id,
        session_id=session_id,
        repo_id=repo.repo_id,
        repo_path=repo.repo_path,
        parent_graph_commit_id=parent_graph_commit_id,
        input_graph_hash=input_graph_hash,
        pipeline_version=PIPELINE_VERSION,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        new_atoms=new_atoms,
        matched_atoms=matched_atoms,
        new_versions=remapped_versions,
        version_edges=[],
        review_candidates=review_candidates,
        unresolved_identity=unresolved,
        metrics=metrics,
        diagnostics=diagnostics,
    )


def _split_new_and_matched_atoms(
    *,
    atoms: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    existing_atoms_by_canonical_key: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not existing_atoms_by_canonical_key:
        return atoms, [], versions
    new_atoms: list[dict[str, Any]] = []
    matched_atoms: list[dict[str, Any]] = []
    atom_id_by_key: dict[str, str] = {}
    for atom in atoms:
        canonical_key = str(atom.get("canonical_key") or "")
        existing = existing_atoms_by_canonical_key.get(canonical_key)
        if existing:
            existing_atom_id = str(existing.get("atom_id") or atom.get("atom_id") or "")
            matched_atoms.append(
                {
                    **atom,
                    "atom_id": existing_atom_id,
                    "planned_atom_id": str(atom.get("atom_id") or ""),
                    "matched_atom_id": existing_atom_id,
                    "match_reason": "canonical_key_exact",
                    "existing_atom": existing,
                }
            )
            atom_id_by_key[canonical_key] = existing_atom_id
        else:
            new_atoms.append(atom)
            atom_id_by_key[canonical_key] = str(atom.get("atom_id") or "")
    remapped_versions: list[dict[str, Any]] = []
    for version in versions:
        metadata = version.get("metadata") if isinstance(version.get("metadata"), dict) else {}
        canonical_key = str(metadata.get("canonical_key") or "")
        atom_id = atom_id_by_key.get(canonical_key) or str(version.get("atom_id") or "")
        remapped_versions.append({**version, "atom_id": atom_id})
    return new_atoms, matched_atoms, remapped_versions


def _repo_identity_from_job(job: dict[str, Any]) -> RepoIdentity:
    resolved = resolve_repo_identity(str(job.get("repo_path") or ""))
    job_repo_id = str(job.get("repo_id") or "").strip()
    if not job_repo_id:
        return resolved
    diagnostics = dict(resolved.diagnostics or {})
    diagnostics["resolved_source"] = resolved.source
    return RepoIdentity(
        repo_id=job_repo_id,
        repo_path=resolved.repo_path,
        source="job_repo_id",
        normalized_remote=resolved.normalized_remote,
        git_root=resolved.git_root,
        diagnostics=diagnostics,
    )


def _nodes(compact_graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = compact_graph.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _exact_atom_previews(
    *,
    nodes: list[dict[str, Any]],
    repo_id: str,
    repo_path: str,
    job_id: str,
    session_id: str,
) -> tuple[list[KnowledgeAtomPreview], list[KnowledgeVersionPreview], list[dict[str, Any]]]:
    by_key: dict[str, KnowledgeAtomPreview] = {}
    versions: list[KnowledgeVersionPreview] = []
    unresolved: list[dict[str, Any]] = []
    for node in nodes:
        atom_kind = _atom_kind(node)
        if atom_kind not in EXACT_ATOM_KINDS:
            continue
        identity = _identity_payload(node, atom_kind)
        if not identity.get("ok"):
            unresolved.append({"node_id": _node_id(node), "node_kind": _node_kind(node), "atom_kind": atom_kind, "reason": identity.get("reason", "missing_identity")})
            continue
        canonical_key = _canonical_key(repo_id, atom_kind, identity)
        if canonical_key not in by_key:
            atom_id = f"katom:{hashlib.sha256(canonical_key.encode('utf-8')).hexdigest()[:32]}"
            by_key[canonical_key] = KnowledgeAtomPreview(
                atom_id=atom_id,
                atom_kind=atom_kind,
                repo_id=repo_id,
                repo_path=repo_path,
                canonical_key=canonical_key,
                canonical_key_version=CANONICAL_KEY_VERSION,
                source_node_ids=[_node_id(node)],
                metadata={k: v for k, v in identity.items() if k != "ok"},
            )
        atom = by_key[canonical_key]
        version_seed = f"{atom.atom_id}|{job_id}|{_node_id(node)}"
        versions.append(
            KnowledgeVersionPreview(
                version_id=f"kver:{hashlib.sha256(version_seed.encode('utf-8')).hexdigest()[:32]}",
                atom_id=atom.atom_id,
                atom_kind=atom_kind,
                status="active",
                job_id=job_id,
                session_id=session_id,
                source_node_ids=[_node_id(node)],
                metadata={"canonical_key": canonical_key, "node_kind": _node_kind(node)},
            )
        )
    return list(by_key.values()), versions, unresolved


def _deferred_atom_counts(atoms: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"symbol": 0, "code_region": 0, "decision": 0, "problem": 0}
    for atom in atoms:
        kind = str(atom.get("atom_kind") or "")
        if kind in counts and kind not in SAFE_APPLY_ATOM_KINDS:
            counts[kind] += 1
    return counts


def _atom_kind(node: dict[str, Any]) -> str:
    kind = _node_kind(node).lower()
    if kind == "commit":
        return "commit"
    if kind in {"fileref"}:
        return "file"
    if kind == "symbolref" and not _central_atom_candidate(node):
        return ""
    if kind == "coderegionref" and not _central_atom_candidate(node):
        return ""
    if kind in {"symbol", "symbolref"}:
        return "symbol"
    if kind in {"codenode", "codeversion", "coderegionref"}:
        return "code_region"
    if kind == "codehunk":
        return "file"
    return ""


def _central_atom_candidate(node: dict[str, Any]) -> bool:
    props = _properties(node)
    return props.get("central_atom_candidate") is True


def _identity_payload(node: dict[str, Any], atom_kind: str) -> dict[str, Any]:
    props = _properties(node)
    if atom_kind == "commit":
        sha = _first(props, "full_sha", "commit_sha", "sha", "short_sha")
        if sha:
            return {"ok": True, "commit_sha": sha.lower()}
        return {"ok": False, "reason": "missing_commit_sha"}
    file_path = _normalize_path(_first(props, "path", "file_path", "normalized_file_path"))
    if not file_path and atom_kind == "symbol":
        symbol_key = _first(props, "symbol_key")
        if "::" in symbol_key:
            file_path = _normalize_path(symbol_key.rsplit("::", 1)[0])
    if atom_kind == "file":
        return {"ok": bool(file_path), "file_path": file_path, "reason": "missing_file_path" if not file_path else ""}
    qualified_name = _first(props, "qualified_name", "symbol_name", "name", "structural_id")
    if not qualified_name and atom_kind == "symbol":
        symbol_key = _first(props, "symbol_key")
        if "::" in symbol_key:
            qualified_name = symbol_key.rsplit("::", 1)[1]
    if atom_kind == "symbol":
        ok = bool(file_path and qualified_name)
        return {"ok": ok, "file_path": file_path, "qualified_name": qualified_name, "reason": "missing_symbol_identity" if not ok else ""}
    ast_kind = _first(props, "symbol_kind", "ast_kind", "node_kind", "node_source")
    ok = bool(file_path and (qualified_name or ast_kind))
    return {
        "ok": ok,
        "file_path": file_path,
        "qualified_name": qualified_name,
        "ast_kind": ast_kind,
        "reason": "missing_code_region_identity" if not ok else "",
    }


def _canonical_key(repo_id: str, atom_kind: str, identity: dict[str, Any]) -> str:
    if atom_kind == "commit":
        return f"commit|{repo_id}|{identity['commit_sha']}"
    if atom_kind == "file":
        return f"file|{repo_id}|{identity['file_path']}"
    if atom_kind == "symbol":
        return f"symbol|{repo_id}|{identity['file_path']}|{identity['qualified_name']}"
    return f"code_region|{repo_id}|{identity['file_path']}|{identity.get('ast_kind', '')}|{identity.get('qualified_name', '')}"


def _node_kind(node: dict[str, Any]) -> str:
    return str(node.get("kind") or node.get("node_kind") or "")


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or node.get("node_id") or node.get("key") or "")


def _properties(node: dict[str, Any]) -> dict[str, Any]:
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    metadata = props.get("metadata") if isinstance(props.get("metadata"), dict) else {}
    raw = {**node, **props, **metadata}
    encoded = props.get("properties_json") or raw.get("properties_json")
    if isinstance(encoded, str) and encoded.strip():
        try:
            decoded = json.loads(encoded)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            raw.update(decoded)
    return raw


def _first(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def read_compact_graph_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
