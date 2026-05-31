from __future__ import annotations

import json
import time

from ...core.config import Settings


def daemon_log(settings: Settings, event: str, **fields: object) -> None:
    record = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        **fields,
    }
    try:
        path = settings.home / "logs" / "daemon.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return
