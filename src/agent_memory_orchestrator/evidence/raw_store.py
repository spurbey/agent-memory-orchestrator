from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from ..domain.evidence.models import RawEvidenceRef


class RawEvidenceStore:
    """Append-only JSONL evidence store.

    Raw hook/transcript payloads live here as evidence. Graph nodes point to
    evidence refs; raw event text is not treated as memory by default.
    """

    def __init__(self, root: Path, *, lock_timeout_seconds: float = 10.0) -> None:
        self.root = root
        self.lock_timeout_seconds = lock_timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        source_app: str,
        event_name: str,
    ) -> RawEvidenceRef:
        created_at = datetime.now(timezone.utc).isoformat()
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        evidence_id = f"raw_{uuid.uuid4().hex}"
        path = self.root / f"{created_at[:10]}.jsonl"
        record = {
            "id": evidence_id,
            "hash": digest,
            "session_id": session_id,
            "source_app": source_app,
            "event_name": event_name,
            "created_at": created_at,
            "payload": payload,
        }
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with _locked_jsonl_path(path, timeout_seconds=self.lock_timeout_seconds):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                offset = handle.tell()
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        return RawEvidenceRef(
            id=evidence_id,
            hash=digest,
            path=str(path.resolve()),
            offset=offset,
            session_id=session_id,
            source_app=source_app,
            event_name=event_name,
            created_at=created_at,
        )


@contextlib.contextmanager
def _locked_jsonl_path(path: Path, *, timeout_seconds: float) -> Any:
    """Inter-process lock for appending one JSONL record.

    Codex hooks run as independent processes and can emit large tool responses
    at nearly the same time. A companion lock file keeps the daily evidence
    JSONL from receiving interleaved fragments.
    """

    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        _ensure_lock_byte(handle)
        lock = _FileLock(handle, timeout_seconds=timeout_seconds)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


def _ensure_lock_byte(handle: Any) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


class _FileLock:
    def __init__(self, handle: Any, *, timeout_seconds: float) -> None:
        self.handle = handle
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.acquired = False

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._try_acquire()
                self.acquired = True
                return
            except BlockingIOError:
                pass
            except OSError as exc:
                if not _is_lock_busy(exc):
                    raise
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for evidence JSONL lock: {self.handle.name}")
            time.sleep(0.025)

    def release(self) -> None:
        if not self.acquired:
            return
        self._release()
        self.acquired = False

    def __enter__(self) -> "_FileLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.release()

    def _try_acquire(self) -> None:
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self) -> None:
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)


def _is_lock_busy(exc: OSError) -> bool:
    if os.name == "nt":
        return getattr(exc, "winerror", None) in {33} or exc.errno in {13}
    return exc.errno in {11, 13}
