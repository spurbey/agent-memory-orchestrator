from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....application.services.semantic_harness.enrichment import attach_agent_checkpoint_review
from ....application.services.semantic_harness.enrichment import ingest_agent_semantic_checkpoint
from ....domain.semantic_harness.projection import build_projection_set
from ....infrastructure.helixdb.semantic_harness import HelixHarnessConfig
from ....infrastructure.helixdb.semantic_harness import HelixHarnessGraphRepository


def add_semantic_checkpoint_subcommands(sub: Any) -> None:
    checkpoint = sub.add_parser("semantic-checkpoint", help="Ingest AMO semantic checkpoint artifacts")
    checkpoint_sub = checkpoint.add_subparsers(dest="semantic_checkpoint_command", required=True)

    ingest = checkpoint_sub.add_parser("ingest", help="Parse, resolve, and review a semantic checkpoint file")
    ingest.add_argument("--file", type=Path, required=True, help="Path to semantic_checkpoint.json.")
    ingest.add_argument("--repo-id", required=True, help="Repo id for the already-warmed Semantic Harness graph.")
    ingest.add_argument("--helix-url", default=None, help="Override local HelixDB URL.")
    ingest.add_argument("--out-dir", type=Path, default=None, help="Directory for pending review artifacts.")
    ingest.add_argument("--mode", choices=("pending", "attach"), default="pending", help="Default is no graph mutation.")

    attach = checkpoint_sub.add_parser("attach", help="Attach accepted facts from a reviewed checkpoint artifact")
    attach.add_argument("--review", type=Path, required=True, help="Path to review_result.json or its artifact directory.")
    attach.add_argument("--repo-id", required=True, help="Repo id for the already-warmed Semantic Harness graph.")
    attach.add_argument("--helix-url", default=None, help="Override local HelixDB URL.")
    attach.add_argument("--out-dir", type=Path, default=None, help="Directory for attach artifacts.")
    attach.add_argument("--status", choices=("accepted-only",), default="accepted-only", help="Only accepted facts attach in v1.")


def handle_semantic_checkpoint_command(args: argparse.Namespace, *, emit: Callable[[object], None]) -> int | None:
    if args.command != "semantic-checkpoint":
        return None

    config = _helix_config(getattr(args, "helix_url", None))
    with HelixHarnessGraphRepository(config) as graph_repo:
        store = graph_repo.load(args.repo_id)
        if store is None:
            emit(
                {
                    "ok": False,
                    "status": "unavailable",
                    "error": "semantic_harness_graph_not_warmed",
                    "repo_id": args.repo_id,
                    "backend": "helix",
                    "helix_url": config.url,
                }
            )
            return 1
        graph = store.to_graph()
        if args.semantic_checkpoint_command == "ingest":
            result = ingest_agent_semantic_checkpoint(
                checkpoint_file=args.file.expanduser().resolve(),
                graph=graph,
                store=store if args.mode == "attach" else None,
                mode=args.mode,
                out_dir=args.out_dir.expanduser().resolve() if args.out_dir else None,
            )
            payload = result.as_dict()
            payload.update({"ok": True, "command": "semantic-checkpoint ingest", "backend": "helix", "helix_url": config.url})
            if args.mode == "attach":
                payload["projection_refresh"] = _projection_refresh(store.to_graph())
            emit(payload)
            return 0
        if args.semantic_checkpoint_command == "attach":
            result = attach_agent_checkpoint_review(
                review_artifact=args.review.expanduser().resolve(),
                graph=graph,
                store=store,
                out_dir=args.out_dir.expanduser().resolve() if args.out_dir else None,
            )
            payload = result.as_dict()
            payload.update({"ok": True, "command": "semantic-checkpoint attach", "backend": "helix", "helix_url": config.url})
            payload["projection_refresh"] = _projection_refresh(store.to_graph())
            emit(payload)
            return 0
    return None


def _helix_config(override: str | None) -> HelixHarnessConfig:
    config = HelixHarnessConfig.from_env()
    return HelixHarnessConfig(url=str(override).rstrip("/"), batch_size=config.batch_size) if override else config


def _projection_refresh(graph: Any) -> dict[str, object]:
    projection = build_projection_set(graph)
    return {
        "projection_id": projection.projection_id,
        "document_count": projection.document_count,
        "semantic_fact_document_count": sum(
            1
            for document in projection.documents
            if document.metadata.get("projection_source") == "semantic_harness_semantic_fact"
        ),
    }


__all__ = [
    "add_semantic_checkpoint_subcommands",
    "handle_semantic_checkpoint_command",
]
