from __future__ import annotations

from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import PRODUCTION_STAGES
from .constants import STAGE_DISPLAY_NAMES
from .constants import V2_STAGES

__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "PIPELINE_VERSION",
    "PRODUCTION_STAGES",
    "ProductionSessionJobRunner",
    "ProductionSessionJobStore",
    "STAGE_DISPLAY_NAMES",
    "V2SessionJobRunner",
    "V2SessionJobStore",
    "V2_STAGES",
    "stage_display_name",
]


def stage_display_name(stage: str) -> str:
    """Return production-facing stage labels while preserving persisted keys."""

    return STAGE_DISPLAY_NAMES.get(stage, stage.replace("_", " ").title())


def __getattr__(name: str):
    if name == "ProductionSessionJobRunner":
        from .runner import ProductionSessionJobRunner

        return ProductionSessionJobRunner
    if name == "ProductionSessionJobStore":
        from .store import ProductionSessionJobStore

        return ProductionSessionJobStore
    if name == "V2SessionJobRunner":
        from .runner import V2SessionJobRunner

        return V2SessionJobRunner
    if name == "V2SessionJobStore":
        from .store import V2SessionJobStore

        return V2SessionJobStore
    raise AttributeError(name)
