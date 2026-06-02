from __future__ import annotations

import time
from collections.abc import Callable

from .....peer import PeerService
from .....peer.agent import PeerAgentService


def _watch_peer_netd_inbox(
    svc: PeerService,
    *,
    limit: int | None,
    interval_seconds: float,
    max_iterations: int = 0,
    fail_fast: bool = False,
    emit_line: Callable[[object], None],
) -> int:
    if interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    iterations = 0
    try:
        while True:
            try:
                emit_line(svc.process_netd_inbox(limit=limit))
            except Exception as exc:
                emit_line({"ok": False, "error": str(exc), "watching": not fail_fast})
                if fail_fast:
                    return 1
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return 0
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        emit_line({"ok": True, "stopped": True, "reason": "interrupted"})
        return 0


def _watch_peer_agent(
    svc: PeerAgentService,
    *,
    limit: int | None,
    interval_seconds: float,
    max_iterations: int = 0,
    fail_fast: bool = False,
    emit_line: Callable[[object], None],
) -> int:
    if interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    iterations = 0
    try:
        while True:
            result = svc.watch_once(limit=limit)
            emit_line(result)
            if fail_fast and not result.get("ok"):
                return 1
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return 0
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        emit_line({"ok": True, "stopped": True, "reason": "interrupted"})
        return 0
