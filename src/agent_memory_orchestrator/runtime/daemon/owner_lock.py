from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

from ...core.config import Settings


class DaemonAlreadyRunning(RuntimeError):
    """Raised when another process already owns this AMO home daemon."""


class DaemonOwnerLock:
    """Cross-process daemon ownership lock for one AMO home.

    Thread locks only coordinate work inside one daemon process. Kuzu file locks
    are process-wide, so AMO also needs a process-level owner guard before
    auto-drain or graph writes can start.
    """

    def __init__(self, lock_path: Path, handle: BinaryIO, metadata_path: Path | None = None) -> None:
        self.lock_path = lock_path
        self.handle = handle
        self.metadata_path = metadata_path
        self.acquired = True

    @classmethod
    def acquire(cls, settings: Settings, *, host: str = "", port: int | None = None) -> "DaemonOwnerLock":
        lock_path = _daemon_owner_lock_path(settings)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        handle = lock_path.open("r+b")
        try:
            _ensure_lock_byte(handle)
            _try_lock_byte(handle)
            _write_daemon_owner_metadata(handle, host=host, port=port)
            metadata_path = _daemon_owner_metadata_path(settings)
            _write_daemon_owner_metadata_file(metadata_path, host=host, port=port)
            return cls(lock_path, handle, metadata_path)
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            _unlock_byte(self.handle)
        finally:
            self.acquired = False
            self.handle.close()
            if self.metadata_path is not None:
                try:
                    self.metadata_path.unlink()
                except OSError:
                    pass

    def __enter__(self) -> "DaemonOwnerLock":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.release()


def _daemon_owner_lock_path(settings: Settings) -> Path:
    return settings.home / ".state" / "daemon-owner.lock"


def _daemon_owner_metadata_path(settings: Settings) -> Path:
    return settings.home / ".state" / "daemon-owner.json"


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


def read_daemon_owner_metadata(settings: Settings) -> dict[str, Any]:
    metadata_path = _daemon_owner_metadata_path(settings)
    try:
        data = metadata_path.read_bytes()
    except OSError:
        return {}
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_daemon_owner_metadata(handle: BinaryIO, *, host: str = "", port: int | None = None) -> None:
    payload = _daemon_owner_payload(host=host, port=port)
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    handle.seek(1)
    handle.truncate(1)
    handle.write(data)
    handle.flush()


def _write_daemon_owner_metadata_file(path: Path, *, host: str = "", port: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _daemon_owner_payload(host=host, port=port)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _daemon_owner_payload(*, host: str = "", port: int | None = None) -> dict[str, object]:
    payload = {
        "pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if host:
        payload["host"] = host
    if port:
        payload["port"] = int(port)
    return payload


def _try_lock_byte(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if _is_daemon_lock_busy(exc):
                raise DaemonAlreadyRunning(f"daemon_already_running:{handle.name}") from exc
            raise
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if _is_daemon_lock_busy(exc):
            raise DaemonAlreadyRunning(f"daemon_already_running:{handle.name}") from exc
        raise


def _unlock_byte(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_daemon_lock_busy(exc: OSError) -> bool:
    if os.name == "nt":
        return getattr(exc, "winerror", None) == 33 or exc.errno in {13}
    return exc.errno in {11, 13}
