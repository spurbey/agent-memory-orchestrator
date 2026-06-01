from __future__ import annotations

from pathlib import Path

from ...core.config import Settings
from ...core.db import connect
from ...core.db import init_schema
from .production_jobs import CentralMergeStoreMixin
from .production_jobs import EnqueueResult
from .production_jobs import SemanticEvalStoreMixin
from .production_jobs import SessionJobStoreMixin
from .production_jobs import default_artifact_dir
from .production_jobs import safe_part
from .production_jobs import stable_job_id
from .production_jobs import utc_now


class ProductionSessionJobStore(SessionJobStoreMixin, CentralMergeStoreMixin, SemanticEvalStoreMixin):
    def __init__(self, settings: Settings, *, db_path: Path | None = None) -> None:
        self.settings = settings
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect(self.db_path)
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()


__all__ = [
    "EnqueueResult",
    "ProductionSessionJobStore",
    "default_artifact_dir",
    "safe_part",
    "stable_job_id",
    "utc_now",
]
