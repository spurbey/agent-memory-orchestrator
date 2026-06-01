from __future__ import annotations

import hashlib
import json
from typing import Any

from ...core.config import Settings
from ...domain.reasoning import qwen_reasoning_contract_hash
from ...domain.reasoning import qwen_reasoning_output_schema
from ...domain.pipeline.constants import CENTRAL_MERGE_PLANNER_VERSION
from ...domain.pipeline.constants import CODE_PARSER_POLICY_VERSION
from ...domain.pipeline.constants import CURATED_GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from ...domain.pipeline.constants import PROMOTION_POLICY_VERSION
from ...domain.pipeline.constants import QUALITY_EVAL_POLICY_VERSION
from ...domain.pipeline.constants import REASONING_CODE_LINK_POLICY_VERSION
from ...domain.pipeline.constants import REASONING_REVIEW_POLICY_VERSION
from ...domain.pipeline.constants import RETRIEVAL_PROJECTION_VERSION
from ...domain.pipeline.constants import SESSION_GRAPH_WRITER_VERSION
from ...domain.pipeline.constants import SYMBOL_VERSION_POLICY_VERSION


def stage_config_payload(settings: Settings, *, stage: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": stage,
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
    }
    if stage == "qwen_reasoning":
        payload.update(
            {
                "qwen_model": settings.qwen_model,
                "qwen_runtime": settings.qwen_runtime,
                "qwen_num_ctx": settings.qwen_num_ctx,
                "qwen_prompt_contract_hash": qwen_reasoning_contract_hash(),
                "stage4_contract_hash": qwen_reasoning_contract_hash(),
                "stage4_schema_hash": hashlib.sha256(json.dumps(qwen_reasoning_output_schema(), sort_keys=True).encode("utf-8")).hexdigest(),
            }
        )
    elif stage == "reasoning_review":
        payload.update(
            {
                "reasoning_review_policy_version": REASONING_REVIEW_POLICY_VERSION,
                "stage4_contract_hash": qwen_reasoning_contract_hash(),
            }
        )
    elif stage == "ast_code_nodes":
        payload["code_parser_policy_version"] = CODE_PARSER_POLICY_VERSION
    elif stage == "symbol_versions":
        payload.update(
            {
                "symbol_version_policy_version": SYMBOL_VERSION_POLICY_VERSION,
                "code_parser_policy_version": CODE_PARSER_POLICY_VERSION,
            }
        )
    elif stage == "reasoning_code_links":
        payload["reasoning_code_link_policy_version"] = REASONING_CODE_LINK_POLICY_VERSION
    elif stage == "kuzu_write":
        payload.update(
            {
                "promotion_policy_version": PROMOTION_POLICY_VERSION,
                "curated_graph_schema_version": CURATED_GRAPH_SCHEMA_VERSION,
                "session_graph_writer_version": SESSION_GRAPH_WRITER_VERSION,
            }
        )
    elif stage == "central_version_merge":
        payload.update(
            {
                "central_merge_planner_version": CENTRAL_MERGE_PLANNER_VERSION,
                "curated_graph_schema_version": CURATED_GRAPH_SCHEMA_VERSION,
            }
        )
    elif stage == "retrieval_docs":
        payload.update(
            {
                "retrieval_projection_version": RETRIEVAL_PROJECTION_VERSION,
                "curated_graph_schema_version": CURATED_GRAPH_SCHEMA_VERSION,
                "retrieval_node_limit": settings.auto_retrieval_node_limit,
                "retrieval_max_doc_chars": settings.auto_retrieval_max_doc_chars,
            }
        )
    elif stage in {"embeddings", "faiss"}:
        payload.update(
            {
                "embedding_model": settings.embedding_model,
                "vector_backend": settings.vector_backend,
            }
        )
    elif stage == "quality_eval":
        payload["quality_eval_policy_version"] = QUALITY_EVAL_POLICY_VERSION
    return payload


def stage_config_hash(settings: Settings, *, stage: str) -> str:
    payload = stage_config_payload(settings, stage=stage)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
