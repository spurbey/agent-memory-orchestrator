from __future__ import annotations

from pathlib import Path
from typing import Any

from ....domain.reasoning import build_stage4_packet_prompt
from ....domain.reasoning import stage4_output_schema
from ..job_runner import OllamaQwenClient
from ..job_runner import PendingModel
from ..job_runner import QwenUnavailable
from ..job_runner import StageResult
from ..packet_helpers import _packet_commit_sha
from ..qwen_checkpoint import _qwen_contract
from ..qwen_checkpoint import _qwen_existing_manifest
from ..qwen_checkpoint import _qwen_existing_results
from ..qwen_checkpoint import _qwen_packet_cache_key
from ..qwen_checkpoint import _qwen_packet_key
from ..qwen_checkpoint import _qwen_reusable_results
from ..qwen_checkpoint import _write_qwen_checkpoint
from ..stage_artifacts import _read_json


def run_qwen_reasoning_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    packets = _read_json(runner._stage_input_artifact(job, "qwen_reasoning", artifact_dir))
    if not isinstance(packets, list):
        raise RuntimeError("work_packets_output_must_be_list")
    output = stage_dir / "stage4_packet_reasoning_results.json"
    manifest = stage_dir / "stage4_packet_reasoning_manifest.json"
    qwen_contract = _qwen_contract(runner.settings)
    packet_keys = [_qwen_packet_key(packet, contract=qwen_contract) for packet in packets if isinstance(packet, dict)]
    existing_results = _qwen_existing_results(output)
    existing_manifest = _qwen_existing_manifest(manifest)
    reusable = _qwen_reusable_results(
        existing_results,
        existing_manifest=existing_manifest,
        packet_keys=packet_keys,
    )
    client = OllamaQwenClient(
        endpoint=runner.settings.qwen_endpoint,
        model=runner.settings.qwen_model,
        timeout_seconds=runner.settings.qwen_extract_timeout_seconds,
        num_ctx=runner.settings.qwen_num_ctx,
    )
    results: list[dict[str, Any]] = []
    reused_count = 0
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        key = _qwen_packet_key(packet, contract=qwen_contract)
        cached = reusable.get(_qwen_packet_cache_key(key))
        if cached is not None:
            results.append(cached)
            reused_count += 1
            continue
        prompt = build_stage4_packet_prompt(packet)
        try:
            parsed = client.generate_json(
                prompt,
                num_predict=900,
                timeout_seconds=runner.settings.qwen_extract_timeout_seconds,
                schema=stage4_output_schema(),
            )
        except QwenUnavailable as exc:
            raise PendingModel("qwen_unavailable", {"packet_id": packet.get("packet_id"), "error": str(exc)}) from exc
        results.append(
            {
                "packet_id": packet.get("packet_id"),
                "commit_sha": _packet_commit_sha(packet),
                "model": runner.settings.qwen_model,
                "runtime": runner.settings.qwen_runtime,
                "contract_hash": qwen_contract["contract_hash"],
                "parsed_output": parsed,
            }
        )
        _write_qwen_checkpoint(output, manifest, results, packet_keys, contract=qwen_contract, complete=False)
    _write_qwen_checkpoint(output, manifest, results, packet_keys, contract=qwen_contract, complete=len(results) == len(packet_keys))
    return StageResult(
        output_path=output,
        diagnostics={
            "packet_count": len(packet_keys),
            "result_count": len(results),
            "reused_result_count": reused_count,
            "generated_result_count": len(results) - reused_count,
            "checkpoint_manifest": str(manifest),
        },
    )
