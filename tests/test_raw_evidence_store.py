from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_memory_orchestrator.evidence.raw_store import RawEvidenceStore


def _append_large_record(root: Path, index: int) -> str:
    evidence = RawEvidenceStore(root)
    ref = evidence.append(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_response": "line\n" * 2000 + f"record={index}",
        },
        session_id="s1",
        source_app="codex",
        event_name="post_tool_use",
    )
    return ref.id


def test_raw_evidence_store_locks_large_jsonl_appends(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    count = 24

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda index: _append_large_record(root, index), range(count)))

    files = list(root.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]

    assert len(rows) == count
    assert {row["id"] for row in rows} == set(ids)
    assert all(row["event_name"] == "post_tool_use" for row in rows)
    assert all(row["payload"]["tool_response"].endswith(tuple(f"record={index}" for index in range(count))) for row in rows)

