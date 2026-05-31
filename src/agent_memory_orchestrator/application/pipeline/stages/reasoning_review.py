from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ....reasoning_graph.reasoning_extraction import review_reasoning_extraction_results
from ..job_runner import StageFailed
from ..job_runner import StageResult
from ..job_runner import _read_json
from ..job_runner import _stage_output


def run_reasoning_review_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    packets = _read_json(_stage_output(artifact_dir, "work_packets"))
    results = _read_json(runner._stage_input_artifact(job, "reasoning_review", artifact_dir))
    review = review_reasoning_extraction_results(
        packets=packets if isinstance(packets, list) else [],
        results=results if isinstance(results, list) else [],
        source_name="production_session_job",
    )
    (stage_dir / "stage4_reasoning_review.json").write_text(json.dumps(review.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    output = stage_dir / "accepted_reasoning_nodes.json"
    output.write_text(json.dumps(list(review.accepted_nodes), indent=2, ensure_ascii=False), encoding="utf-8")
    if review.summary.get("stage_acceptance") == "FAIL":
        raise StageFailed(
            "reasoning_review_acceptance_failed",
            {
                "summary": review.summary,
                "review_artifact": str(stage_dir / "stage4_reasoning_review.json"),
                "accepted_nodes_artifact": str(output),
                "note": "Curated graph promotion is blocked only when Qwen reasoning has structural errors. Review-only output can still produce deterministic commit/file memory.",
            },
        )
    return StageResult(output_path=output, diagnostics={"summary": review.summary})

