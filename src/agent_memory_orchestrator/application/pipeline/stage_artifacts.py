from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return path_hash(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def path_hash(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    if path.is_dir():
        rows: list[str] = []
        for child in sorted(path.rglob("*")):
            if child.is_file():
                rows.append(f"{child.relative_to(path)}:{hashlib.sha256(child.read_bytes()).hexdigest()}")
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return ""


def _stage_output(artifact_dir: Path, stage: str) -> Path:
    if stage == "qwen_reasoning":
        output = artifact_dir / stage / "results.json"
        if output.exists():
            return output
        return artifact_dir / stage / "stage4_packet_reasoning_results.json"
    candidates = {
        "evidence_view": "reasoning_evidence_view.json",
        "work_packets": "reasoning_work_packets.json",
        "reasoning_review": "accepted_reasoning_nodes.json",
        "git_hunks": "code_hunks.json",
        "ast_code_nodes": "code_nodes.json",
        "symbol_versions": "symbol_versions.json",
        "reasoning_code_links": "graph_edges.json",
        "kuzu_write": "kuzu_write_result.json",
        "central_version_merge": "merge_plan.json",
        "retrieval_docs": "retrieval_docs_result.json",
        "embeddings": "embeddings_result.json",
        "faiss": "faiss_result.json",
        "quality_eval": "quality_eval.json",
    }
    return artifact_dir / stage / candidates[stage]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records), encoding="utf-8")
