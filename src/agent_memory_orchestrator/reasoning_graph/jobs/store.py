"""Compatibility wrapper for production job persistence.

New code should import from agent_memory_orchestrator.infrastructure.sqlite.production_job_store.
"""

from __future__ import annotations

from ...infrastructure.sqlite.production_job_store import EnqueueResult
from ...infrastructure.sqlite.production_job_store import JOB_STATUSES
from ...infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from ...infrastructure.sqlite.production_job_store import STAGE_STATUSES
from ...infrastructure.sqlite.production_job_store import default_artifact_dir
from ...infrastructure.sqlite.production_job_store import graph_view_id
from ...infrastructure.sqlite.production_job_store import safe_part
from ...infrastructure.sqlite.production_job_store import stable_job_id
from ...infrastructure.sqlite.production_job_store import utc_now

__all__ = [
    "EnqueueResult",
    "JOB_STATUSES",
    "ProductionSessionJobStore",
    "STAGE_STATUSES",
    "default_artifact_dir",
    "graph_view_id",
    "safe_part",
    "stable_job_id",
    "utc_now",
]
