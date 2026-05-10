from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .common import stable_json as _json


class MemorySnapshotMixin:
    def export_snapshot(self, out_path: Path, session_id: str | None = None) -> int:
        tables = [
            "sessions",
            "events",
            "chunks",
            "memory_units",
            "memories",
            "memory_vectors",
            "entities",
            "kg_edges",
            "session_summaries",
            "index_versions",
            "pipeline_runs",
            "extraction_runs",
            "retrieval_runs",
            "retrieval_candidates",
            "consolidation_decisions",
            "approval_decisions",
            "orchestration_rounds",
            "orchestration_decisions",
        ]
        rows_written = 0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for table in tables:
                for row in self._rows_for_export(table, session_id):
                    f.write(_json({"table": table, "row": dict(row)}) + "\n")
                    rows_written += 1
        return rows_written

    def import_snapshot(self, in_path: Path) -> int:
        inserted = 0
        with in_path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                table = payload.get("table")
                row = payload.get("row")
                if not table or not isinstance(row, dict):
                    continue
                columns = list(row.keys())
                col_clause = ", ".join(columns)
                val_clause = ", ".join(["?"] * len(columns))
                self.conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_clause}) VALUES ({val_clause})",
                    tuple(row[col] for col in columns),
                )
                inserted += 1
        self.conn.commit()
        return inserted

    def _rows_for_export(self, table: str, session_id: str | None) -> list[sqlite3.Row]:
        if not session_id:
            return list(self.conn.execute(f"SELECT * FROM {table}").fetchall())
        if table == "sessions":
            return list(self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchall())
        session_tables = {
            "events",
            "chunks",
            "memory_units",
            "memories",
            "session_summaries",
            "pipeline_runs",
            "extraction_runs",
            "retrieval_runs",
            "approval_decisions",
            "orchestration_rounds",
            "orchestration_decisions",
        }
        if table in session_tables:
            return list(self.conn.execute(f"SELECT * FROM {table} WHERE session_id = ?", (session_id,)).fetchall())
        if table == "memory_vectors":
            return list(
                self.conn.execute(
                    """
                    SELECT mv.*
                    FROM memory_vectors mv
                    JOIN memory_units mu ON mu.id = mv.memory_id
                    WHERE mu.session_id = ?
                    """,
                    (session_id,),
                ).fetchall()
            )
        return list(self.conn.execute(f"SELECT * FROM {table}").fetchall())

