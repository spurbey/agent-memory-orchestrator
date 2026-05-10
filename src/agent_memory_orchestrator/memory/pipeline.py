from __future__ import annotations

from .common import elapsed_ms as _elapsed_ms
from .common import new_id as _id
from .common import stable_json as _json
from .common import utc_now as _utc_now


class MemoryPipelineMixin:
    def _start_pipeline_run(self, run_type: str, session_id: str | None, source_event_id: str | None) -> str:
        run_id = _id("prun")
        self.conn.execute(
            """
            INSERT INTO pipeline_runs(id, run_type, session_id, source_event_id, status, started_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (run_id, run_type, session_id, source_event_id, "running", _utc_now()),
        )
        self.conn.commit()
        return run_id

    def _finish_pipeline_run(
        self,
        run_id: str,
        status: str,
        started: float,
        metrics: dict,
        error: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, finished_at = ?, duration_ms = ?, metrics_json = ?, error = ?
            WHERE id = ?
            """,
            (status, _utc_now(), _elapsed_ms(started), _json(metrics), error, run_id),
        )
        self.conn.commit()

