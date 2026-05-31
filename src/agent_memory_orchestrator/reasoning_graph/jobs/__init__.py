from __future__ import annotations

from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from ...domain.pipeline.constants import PRODUCTION_STAGES
from ...domain.pipeline.constants import STAGE_DISPLAY_NAMES

__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "PIPELINE_VERSION",
    "PRODUCTION_STAGES",
    "ProductionSessionJobRunner",
    "ProductionSessionJobStore",
    "STAGE_DISPLAY_NAMES",
    "stage_display_name",
]


def stage_display_name(stage: str) -> str:
    """Return production-facing stage labels while preserving persisted keys."""

    return STAGE_DISPLAY_NAMES.get(stage, stage.replace("_", " ").title())


def __getattr__(name: str):
    if name == "ProductionSessionJobRunner":
        from ...application.pipeline.job_runner import ProductionSessionJobRunner

        return ProductionSessionJobRunner
    if name == "ProductionSessionJobStore":
        from .store import ProductionSessionJobStore

        return ProductionSessionJobStore
    raise AttributeError(name)
