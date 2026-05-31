from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...code_analysis import extract_code_nodes_from_commit
from ..runner import StageResult
from ..runner import _code_node_record
from ..runner import _hunk_record
from ..runner import _packet_evidence_refs
from ..runner import _packet_full_sha
from ..runner import _read_json
from ..runner import _relationship_edges
from ..runner import _stage_output
from ..runner import _symbol_versions


def run_git_hunks_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del runner
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


def run_ast_code_nodes_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del runner
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


def run_symbol_versions_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del runner, job
    code_nodes = _read_json(_stage_output(artifact_dir, "ast_code_nodes"))
    symbols, versions, edges = _symbol_versions(code_nodes if isinstance(code_nodes, list) else [])
    output = stage_dir / "symbol_versions.json"
    output.write_text(json.dumps({"symbols": symbols, "code_versions": versions, "edges": edges}, indent=2, ensure_ascii=False), encoding="utf-8")
    return StageResult(output_path=output, diagnostics={"symbol_count": len(symbols), "code_version_count": len(versions), "edge_count": len(edges)})


def run_reasoning_code_links_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del runner, job
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
