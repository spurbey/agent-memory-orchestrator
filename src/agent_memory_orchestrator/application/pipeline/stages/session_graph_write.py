from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ....domain.pipeline.constants import RESET_MARKER_KEY
from ..graph_writer import build_compact_session_graph
from ..graph_writer import write_compact_session_graph
from ..job_runner import StageResult
from ..job_runner import _commit_nodes
from ..job_runner import _evidence_ref_nodes
from ..job_runner import _promotion_summary
from ..job_runner import _read_json
from ..job_runner import _should_write_artifact_kuzu
from ..job_runner import _stage_output
from ..job_runner import _versioned_items
from ..job_runner import _write_curated_session_graph_to_central
from ..job_runner import require_complete_production_marker
from ..promotion import build_curated_session_graph


def run_session_graph_write_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    require_complete_production_marker(runner.job_store.marker(RESET_MARKER_KEY))
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
    curated = build_curated_session_graph(
        packets=packets,
        reasoning_nodes=reasoning_nodes,
        evidence_refs=evidence_refs,
        commit_nodes=commits,
        code_hunks=hunk_nodes,
        code_nodes=code_nodes,
    )
    manifest = stage_dir / "compact_graph_manifest.json"
    manifest.write_text(json.dumps(graph.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    curated_manifest = stage_dir / "curated_graph_manifest.json"
    curated_manifest.write_text(json.dumps(curated.graph.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    (stage_dir / "curation_audit.json").write_text(json.dumps(curated.audit, indent=2, ensure_ascii=False), encoding="utf-8")
    artifact_graph_path = stage_dir / "session_graph.kuzu"
    artifact_graph_written = _should_write_artifact_kuzu(curated.graph.inventory)
    if artifact_graph_written:
        write_compact_session_graph(
            graph_path=artifact_graph_path,
            nodes=list(curated.graph.nodes),
            edges=list(curated.graph.edges),
            force=True,
        )
    central_write = _write_curated_session_graph_to_central(
        runner.graph_store_factory,
        runner.settings.graph_path,
        nodes=curated.graph.nodes,
        edges=curated.graph.edges,
        job=job,
    )
    diagnostics = {
        **curated.graph.inventory,
        "trace_inventory": graph.inventory,
        "curated_inventory": curated.graph.inventory,
        "promotion": _promotion_summary(curated.audit),
        "central_write": central_write,
    }
    output = stage_dir / "kuzu_write_result.json"
    output.write_text(
        json.dumps(
            {
                "ok": graph.inventory.get("unresolved_edge_count") == 0,
                "graph_path": str(runner.settings.graph_path),
                "artifact_graph_path": str(artifact_graph_path) if artifact_graph_written else "",
                "artifact_graph_written": artifact_graph_written,
                "central_write": central_write,
                "inventory": curated.graph.inventory,
                "trace_inventory": graph.inventory,
                "curated_manifest_path": str(curated_manifest),
                "promotion": _promotion_summary(curated.audit),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return StageResult(output_path=output, diagnostics=diagnostics)

