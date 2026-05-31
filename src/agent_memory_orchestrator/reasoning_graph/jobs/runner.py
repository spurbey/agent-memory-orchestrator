from __future__ import annotations

from ...application.pipeline import job_runner as _impl
from ...application.pipeline.job_runner import OllamaQwenClient
from ...application.pipeline.job_runner import PendingModel
from ...application.pipeline.job_runner import ProductionSessionJobRunner
from ...application.pipeline.job_runner import QwenUnavailable
from ...application.pipeline.job_runner import StageFailed
from ...application.pipeline.job_runner import StageResult
from ...application.pipeline.job_runner import file_sha256
from ...application.pipeline.job_runner import path_hash
from ...application.pipeline.job_runner import require_complete_production_marker
from ...application.pipeline.job_runner import stage_config_hash
from ...application.pipeline.job_runner import stage_config_payload

# Compatibility module. Production code should import from
# agent_memory_orchestrator.application.pipeline.job_runner.
_central_merge_quality_result = _impl._central_merge_quality_result
_quality_issues = _impl._quality_issues
_quality_readiness = _impl._quality_readiness

__all__ = [
    "OllamaQwenClient",
    "PendingModel",
    "ProductionSessionJobRunner",
    "QwenUnavailable",
    "StageFailed",
    "StageResult",
    "file_sha256",
    "path_hash",
    "require_complete_production_marker",
    "stage_config_hash",
    "stage_config_payload",
]


def __getattr__(name: str):
    return getattr(_impl, name)
