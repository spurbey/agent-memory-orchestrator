from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ...app.client import DaemonClient
from ...app.client import DaemonUnavailable
from ...core.config import Settings
from ...core.db import connect
from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import RESET_MARKER_KEY
from .store import V2SessionJobStore


def reset_production_v2_storage(
    settings: Settings,
    *,
    backup: bool,
    clean_graph: bool,
    clean_retrieval: bool,
    force_if_daemon_running: bool = False,
) -> dict[str, Any]:
    if not backup:
        raise ValueError("--backup is required for v2-reset-production")
    daemon = _daemon_status(settings)
    if daemon.get("running") and not force_if_daemon_running:
        raise RuntimeError("daemon_running: stop amo-daemon or pass --force-if-daemon-running")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = settings.home / "backups" / f"v2-reset-production-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "created_at": timestamp,
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "source": {
            "graph_path": str(settings.graph_path),
            "retrieval_db_path": str(settings.retrieval_db_path),
            "db_path": str(settings.db_path),
        },
        "copied": [],
        "clean_graph": clean_graph,
        "clean_retrieval": clean_retrieval,
    }

    _backup_path(settings.graph_path, backup_dir / "graph", manifest)
    _backup_path(settings.retrieval_db_path, backup_dir / "retrieval_db", manifest)
    _backup_path(settings.db_path, backup_dir / "main_db", manifest)
    _backup_path(settings.home / "config.json", backup_dir / "config.json", manifest)
    _backup_path(settings.home / ".state" / "production_v2_reset.json", backup_dir / "production_v2_reset.json", manifest)
    _backup_path(_faiss_index_dir(settings), backup_dir / "faiss_indexes", manifest)
    _write_manifest(backup_dir, manifest)
    _verify_manifest(backup_dir)

    cleaned: dict[str, Any] = {"graph": False, "retrieval": False, "faiss": False}
    if clean_graph:
        _remove_path(settings.graph_path)
        settings.graph_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned["graph"] = True
    if clean_retrieval:
        _clean_retrieval_tables(settings.retrieval_db_path)
        index_dir = _faiss_index_dir(settings)
        _remove_path(index_dir)
        cleaned["retrieval"] = True
        cleaned["faiss"] = True

    marker = {
        "production_v2_reset_applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "backup_path": str(backup_dir.resolve()),
        "cleaned": cleaned,
    }
    store = V2SessionJobStore(settings)
    try:
        store.upsert_marker(RESET_MARKER_KEY, marker)
    finally:
        store.close()
    (settings.home / ".state").mkdir(parents=True, exist_ok=True)
    (settings.home / ".state" / "production_v2_reset.json").write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "backup_path": str(backup_dir), "marker": marker}


def _daemon_status(settings: Settings) -> dict[str, Any]:
    try:
        health = DaemonClient.from_settings(settings, timeout_seconds=1.0).health()
    except DaemonUnavailable:
        return {"running": False}
    except Exception:
        return {"running": False}
    return {"running": bool(health.get("ok")), "health": health}


def _backup_path(source: Path, target: Path, manifest: dict[str, Any]) -> None:
    source = source.resolve()
    if not source.exists():
        return
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest["copied"].append({"source": str(source), "target": str(target.resolve())})


def _write_manifest(backup_dir: Path, manifest: dict[str, Any]) -> None:
    (backup_dir / "backup_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _verify_manifest(backup_dir: Path) -> None:
    manifest_path = backup_dir / "backup_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("backup_manifest_missing")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    copied = payload.get("copied")
    if not isinstance(copied, list):
        raise RuntimeError("backup_manifest_invalid")
    for row in copied:
        if isinstance(row, dict) and row.get("target") and not Path(str(row["target"])).exists():
            raise RuntimeError(f"backup_target_missing:{row['target']}")


def _remove_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()


def _clean_retrieval_tables(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        for table in ("retrieval_documents_fts", "retrieval_documents", "graph_embeddings"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def _faiss_index_dir(settings: Settings) -> Path:
    return settings.retrieval_db_path.parent / "indexes" / settings.retrieval_db_path.stem
