from __future__ import annotations

import json
from typing import Any

from .base import utc_now


class SemanticEvalStoreMixin:
    def record_semantic_eval_run(self, *, run_id: str, case_set: str, fixture_path: str, status: str, metrics: dict[str, Any], diagnostics: dict[str, Any] | None = None) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO v2_semantic_eval_runs(run_id, case_set, fixture_path, status, metrics_json, diagnostics_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              status=excluded.status,
              metrics_json=excluded.metrics_json,
              diagnostics_json=excluded.diagnostics_json,
              updated_at=excluded.updated_at
            """,
            (run_id, case_set, fixture_path, status, json.dumps(metrics, sort_keys=True), json.dumps(diagnostics or {}, sort_keys=True), now, now),
        )
        self.conn.commit()


__all__ = ["SemanticEvalStoreMixin"]
