from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def stable_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
