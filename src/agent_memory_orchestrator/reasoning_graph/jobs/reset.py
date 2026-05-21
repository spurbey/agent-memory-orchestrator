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
from ...graph.store import KuzuGraphStore
from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import RESET_MARKER_KEY
from .store import V2SessionJobStore


def initialize_fresh_v2_production_storage(settings: Settings) -> dict[str, Any]:
    """Mark a fresh, empty production install as V2-ready.

    This is intentionally non-destructive. It exists for new devices: they have
    no pre-V2 graph/retrieval data to clean, so forcing the operator through a
    reset command is wrong. Existing non-empty stores still require explicit
    backup-first reset/adoption.
    """
    store = V2SessionJobStore(settings)
    try:
        existing = store.marker(RESET_MARKER_KEY)
    finally:
        store.close()
    if existing is not None:
        return {"ok": True, "created": False, "reason": "marker_exists", "marker": existing}

    graph = _validate_empty_graph_store(settings)
    retrieval = _validate_empty_retrieval_store(settings)
    if not graph.get("empty") or not retrieval.get("empty"):
        raise RuntimeError(
            "v2_fresh_init_refused_non_empty_stores:"
            + json.dumps({"graph": graph, "retrieval": retrieval}, sort_keys=True)
        )

    marker = {
        "production_v2_initialized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "fresh_install": True,
        "backup_path": "",
        "cleaned": {"graph": True, "retrieval": True, "faiss": True},
        "validated": {"graph_empty": True, "retrieval_empty": True},
        "validation": {"graph": graph, "retrieval": retrieval},
    }
    _write_marker(settings, marker)
    return {"ok": True, "created": True, "reason": "fresh_empty_stores", "marker": marker}


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
    if not clean_graph or not clean_retrieval:
        raise ValueError("--clean-graph and --clean-retrieval are required to apply production V2 reset")
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


def adopt_existing_v2_production_storage(
    settings: Settings,
    *,
    backup: bool,
    validate_graph: bool,
    validate_retrieval: bool,
    force_if_daemon_running: bool = False,
) -> dict[str, Any]:
    """Mark existing production stores as V2-ready without deleting them.

    This is intentionally explicit and backup-first. It is for the case where
    production already contains the validated V2 reset graph/retrieval output
    and the operator wants the V2 job runner to resume without wiping it.
    """
    if not backup:
        raise ValueError("--backup is required for v2-adopt-production")
    if not validate_graph or not validate_retrieval:
        raise ValueError("--validate-graph and --validate-retrieval are required to adopt existing V2 production stores")
    daemon = _daemon_status(settings)
    if daemon.get("running") and not force_if_daemon_running:
        raise RuntimeError("daemon_running: stop amo-daemon or pass --force-if-daemon-running")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir, manifest = _backup_production_paths(settings, label=f"v2-adopt-production-{timestamp}", timestamp=timestamp)

    validation = {
        "graph": _validate_existing_graph_store(settings),
        "retrieval": _validate_existing_retrieval_store(settings),
    }
    manifest["validation"] = validation
    _write_manifest(backup_dir, manifest)
    _verify_manifest(backup_dir)

    marker = {
        "production_v2_reset_applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "backup_path": str(backup_dir.resolve()),
        "adopted_existing_v2": True,
        "validated": {"graph": True, "retrieval": True},
        "validation": validation,
        "cleaned": {"graph": False, "retrieval": False, "faiss": False},
    }
    _write_marker(settings, marker)
    return {"ok": True, "backup_path": str(backup_dir), "marker": marker}


def _daemon_status(settings: Settings) -> dict[str, Any]:
    try:
        health = DaemonClient.from_settings(settings, timeout_seconds=1.0).health()
    except DaemonUnavailable:
        return {"running": False}
    except Exception:
        return {"running": False}
    return {"running": bool(health.get("ok")), "health": health}


def _backup_production_paths(settings: Settings, *, label: str, timestamp: str) -> tuple[Path, dict[str, Any]]:
    backup_dir = settings.home / "backups" / label
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
    }

    _backup_path(settings.graph_path, backup_dir / "graph", manifest)
    _backup_path(settings.retrieval_db_path, backup_dir / "retrieval_db", manifest)
    _backup_path(settings.db_path, backup_dir / "main_db", manifest)
    _backup_path(settings.home / "config.json", backup_dir / "config.json", manifest)
    _backup_path(settings.home / ".state" / "production_v2_reset.json", backup_dir / "production_v2_reset.json", manifest)
    _backup_path(_faiss_index_dir(settings), backup_dir / "faiss_indexes", manifest)
    return backup_dir, manifest


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


def _write_marker(settings: Settings, marker: dict[str, Any]) -> None:
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


def _validate_existing_graph_store(settings: Settings) -> dict[str, Any]:
    path = settings.graph_path
    exists = path.exists()
    non_empty = _path_has_content(path)
    result = {
        "ok": bool(exists and non_empty),
        "path": str(path),
        "exists": exists,
        "non_empty": non_empty,
        "check": "filesystem_non_empty",
    }
    if not result["ok"]:
        raise RuntimeError(f"v2_adopt_graph_validation_failed:{json.dumps(result, sort_keys=True)}")
    return result


def _validate_empty_graph_store(settings: Settings) -> dict[str, Any]:
    graph = KuzuGraphStore(settings.graph_path)
    try:
        graph.init_schema()
        status = graph.merge_status()
        counts = status.get("counts") if isinstance(status, dict) else {}
        node_count = sum(int(value) for value in counts.values()) if isinstance(counts, dict) else 0
        edge_sample_count = len(graph.list_edges(limit=1))
    finally:
        graph.close()
    return {
        "ok": node_count == 0 and edge_sample_count == 0,
        "empty": node_count == 0 and edge_sample_count == 0,
        "path": str(settings.graph_path),
        "node_count": node_count,
        "edge_sample_count": edge_sample_count,
        "check": "kuzu_node_edge_empty",
    }


def _validate_existing_retrieval_store(settings: Settings) -> dict[str, Any]:
    path = settings.retrieval_db_path
    if not path.exists():
        result = {"ok": False, "path": str(path), "exists": False}
        raise RuntimeError(f"v2_adopt_retrieval_validation_failed:{json.dumps(result, sort_keys=True)}")
    conn = connect(path)
    try:
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='retrieval_documents'"
        ).fetchone()
        doc_count = 0
        if table_row is not None:
            doc_count = int(conn.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone()[0])
    finally:
        conn.close()
    result = {
        "ok": table_row is not None and doc_count > 0,
        "path": str(path),
        "exists": True,
        "retrieval_document_count": doc_count,
        "check": "retrieval_documents_non_empty",
    }
    if not result["ok"]:
        raise RuntimeError(f"v2_adopt_retrieval_validation_failed:{json.dumps(result, sort_keys=True)}")
    return result


def _validate_empty_retrieval_store(settings: Settings) -> dict[str, Any]:
    path = settings.retrieval_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        doc_count = _table_count(conn, "retrieval_documents")
        embedding_count = _table_count(conn, "graph_embeddings")
    finally:
        conn.close()
    return {
        "ok": doc_count == 0 and embedding_count == 0,
        "empty": doc_count == 0 and embedding_count == 0,
        "path": str(path),
        "retrieval_document_count": doc_count,
        "embedding_count": embedding_count,
        "check": "retrieval_and_embedding_tables_empty",
    }


def _table_count(conn: Any, table: str) -> int:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _path_has_content(path: Path) -> bool:
    if path.is_file():
        return path.stat().st_size > 0
    if path.is_dir():
        return any(path.iterdir())
    return False
