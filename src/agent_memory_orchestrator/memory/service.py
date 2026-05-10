from __future__ import annotations

import sqlite3

from ..core.config import Settings
from ..core.db import connect, init_schema
from .hooks import MemoryHooksMixin
from .ingest import MemoryIngestMixin
from .pipeline import MemoryPipelineMixin
from .retrieval import MemoryRetrievalMixin
from .snapshots import MemorySnapshotMixin
from .storage import MemoryStorageMixin
from .summary import MemorySummaryMixin
from .views import MemoryViewsMixin

class MemoryService(
    MemoryIngestMixin,
    MemoryStorageMixin,
    MemoryRetrievalMixin,
    MemoryViewsMixin,
    MemorySnapshotMixin,
    MemoryPipelineMixin,
    MemorySummaryMixin,
    MemoryHooksMixin,
):
    def __init__(self, settings: Settings, conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings
        self.conn = conn or connect(settings.db_path)
        self.defer_vectors = False

    def init_db(self) -> None:
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

