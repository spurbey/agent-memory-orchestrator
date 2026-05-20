from __future__ import annotations

PIPELINE_VERSION = "v2-reset-2026-05"
GRAPH_SCHEMA_VERSION = "v2"

V2_STAGES: tuple[str, ...] = (
    "evidence_view",
    "work_packets",
    "qwen_reasoning",
    "reasoning_review",
    "git_hunks",
    "ast_code_nodes",
    "symbol_versions",
    "reasoning_code_links",
    "kuzu_write",
    "retrieval_docs",
    "embeddings",
    "faiss",
    "quality_eval",
)

RESET_MARKER_KEY = "production_v2_reset"
