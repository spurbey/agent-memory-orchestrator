from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from ...domain.reasoning import stage4_contract_hash
from ...domain.reasoning import stage4_output_schema
from .packet_helpers import _packet_commit_sha
from .stage_artifacts import _read_json


def _qwen_contract(settings: Settings) -> dict[str, str]:
    payload = {
        "model": settings.qwen_model,
        "runtime": settings.qwen_runtime,
        "num_ctx": settings.qwen_num_ctx,
        "stage4_contract_hash": stage4_contract_hash(),
        "stage4_schema_hash": hashlib.sha256(json.dumps(stage4_output_schema(), sort_keys=True).encode("utf-8")).hexdigest(),
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
    }
    return {**payload, "contract_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()}


def _qwen_packet_key(packet: dict[str, Any], *, contract: dict[str, str]) -> dict[str, str]:
    return {
        "packet_id": str(packet.get("packet_id") or ""),
        "commit_sha": _packet_commit_sha(packet),
        "packet_hash": hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "contract_hash": str(contract.get("contract_hash") or ""),
    }


def _qwen_packet_cache_key(packet_key: dict[str, str]) -> tuple[str, str, str, str]:
    return (packet_key["packet_id"], packet_key["commit_sha"], packet_key["packet_hash"], packet_key["contract_hash"])


def _qwen_existing_results(output: Path) -> list[dict[str, Any]]:
    if not output.exists():
        return []
    try:
        loaded = _read_json(output)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _qwen_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _qwen_reusable_results(
    existing_results: list[dict[str, Any]],
    *,
    existing_manifest: dict[str, Any],
    packet_keys: list[dict[str, str]],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not existing_results:
        return {}
    manifest_packets = existing_manifest.get("packets")
    manifest_by_result_key: dict[tuple[str, str], tuple[str, str]] = {}
    if isinstance(manifest_packets, list):
        for item in manifest_packets:
            if not isinstance(item, dict):
                continue
            manifest_by_result_key[(str(item.get("packet_id") or ""), str(item.get("commit_sha") or ""))] = (
                str(item.get("packet_hash") or ""),
                str(item.get("contract_hash") or ""),
            )

    current_by_legacy_key = {(item["packet_id"], item["commit_sha"]): item for item in packet_keys}
    reusable: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for result in existing_results:
        packet_id = str(result.get("packet_id") or "")
        commit_sha = str(result.get("commit_sha") or "")
        if not packet_id:
            continue
        current = current_by_legacy_key.get((packet_id, commit_sha))
        if current is None:
            continue
        manifest_hash, manifest_contract = manifest_by_result_key.get((packet_id, commit_sha), ("", ""))
        if not manifest_hash or not manifest_contract:
            continue
        if manifest_hash != current["packet_hash"] or manifest_contract != current["contract_hash"]:
            continue
        if str(result.get("contract_hash") or manifest_contract) != current["contract_hash"]:
            continue
        if not isinstance(result.get("parsed_output"), dict):
            continue
        reusable[_qwen_packet_cache_key(current)] = result
    return reusable


def _write_qwen_checkpoint(
    output: Path,
    manifest: Path,
    results: list[dict[str, Any]],
    packet_keys: list[dict[str, str]],
    *,
    contract: dict[str, str],
    complete: bool,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output.with_suffix(output.suffix + ".tmp")
    manifest_tmp = manifest.with_suffix(manifest.suffix + ".tmp")
    output_tmp.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest_payload = {
        "complete": complete,
        "result_count": len(results),
        "packet_count": len(packet_keys),
        "contract": contract,
        "packets": packet_keys[: len(results)],
    }
    manifest_tmp.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    output_tmp.replace(output)
    manifest_tmp.replace(manifest)
