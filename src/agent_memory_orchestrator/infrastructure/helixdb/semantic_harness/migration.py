from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness import graph_snapshot_identity
from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteHarnessGraphRepository

from .config import HelixHarnessConfig
from .repository import HelixHarnessGraphRepository


def migrate_sqlite_repo_to_helix(
    *,
    repo_id: str,
    sqlite_path: str | Path,
    helix_config: HelixHarnessConfig | None = None,
) -> dict[str, Any]:
    """One-time migration with deterministic graph-snapshot verification."""

    with SQLiteHarnessGraphRepository(sqlite_path) as sqlite_repository:
        source_store = sqlite_repository.load(repo_id)
        if source_store is None:
            raise ValueError(f"sqlite_repo_not_found:{repo_id}")
        source_graph = source_store.to_graph()

    config = helix_config or HelixHarnessConfig.from_env()
    with HelixHarnessGraphRepository(config) as helix_repository:
        if not helix_repository.healthy():
            raise RuntimeError(f"helix_unavailable:{config.url}")
        helix_repository.replace_from_graph(source_graph)
        migrated_store = helix_repository.load(repo_id)
        if migrated_store is None:
            raise RuntimeError(f"helix_repo_not_found_after_write:{repo_id}")
        migrated_graph = migrated_store.to_graph()

    source_snapshot = graph_snapshot_identity(source_graph)
    migrated_snapshot = graph_snapshot_identity(migrated_graph)
    verified = (
        len(source_graph.nodes) == len(migrated_graph.nodes)
        and len(source_graph.edges) == len(migrated_graph.edges)
        and source_snapshot.graph_snapshot_id == migrated_snapshot.graph_snapshot_id
    )
    if not verified:
        raise RuntimeError(
            "helix_migration_verification_failed:"
            f"nodes={len(source_graph.nodes)}/{len(migrated_graph.nodes)}:"
            f"edges={len(source_graph.edges)}/{len(migrated_graph.edges)}:"
            "snapshots="
            f"{source_snapshot.graph_snapshot_id}/{migrated_snapshot.graph_snapshot_id}"
        )
    return {
        "status": "migrated",
        "verified": True,
        "repo_id": repo_id,
        "source": "sqlite",
        "target": "helix",
        "helix_url": config.url,
        "node_count": len(migrated_graph.nodes),
        "edge_count": len(migrated_graph.edges),
        "graph_snapshot_id": migrated_snapshot.graph_snapshot_id,
    }


__all__ = ["migrate_sqlite_repo_to_helix"]
