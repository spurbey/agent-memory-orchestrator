from __future__ import annotations

from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import V2_STAGES

__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "PIPELINE_VERSION",
    "V2SessionJobRunner",
    "V2SessionJobStore",
    "V2_STAGES",
]


def __getattr__(name: str):
    if name == "V2SessionJobRunner":
        from .runner import V2SessionJobRunner

        return V2SessionJobRunner
    if name == "V2SessionJobStore":
        from .store import V2SessionJobStore

        return V2SessionJobStore
    raise AttributeError(name)
