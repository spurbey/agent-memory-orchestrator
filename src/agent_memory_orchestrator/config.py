from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_config_file(home: Path) -> dict:
    config_path = Path(os.getenv("AMO_CONFIG_PATH", home / "config.json"))
    if not config_path.is_absolute():
        config_path = (home / config_path).resolve()
    if not config_path.exists():
        return {}
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"AMO config must be a JSON object: {config_path}")
    return payload


def _setting(config: dict, key: str, default: object, env_key: str | None = None) -> object:
    env_name = env_key or f"AMO_{key.upper()}"
    if env_name in os.environ:
        return os.environ[env_name]
    if key in config:
        return config[key]
    settings = config.get("settings")
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    return default


@dataclass(frozen=True)
class Settings:
    home: Path
    db_path: Path
    export_dir: Path
    local_only: bool
    mcp_transport: str
    mcp_host: str
    mcp_port: int
    embedding_dims: int
    embedding_model: str
    reranker_model: str
    vector_backend: str
    approval_mode: str
    owner_user_id: str
    workspace_id: str
    project_id: str
    visibility_scope: str
    sensitivity_level: str
    consensus_threshold: float
    max_review_rounds: int
    context_budget: int = 2500
    reranker_backend: str = "auto"
    rerank_top_k: int = 50
    rerank_max_chars: int = 1800

    @classmethod
    def load(cls) -> "Settings":
        home = Path(os.getenv("AMO_HOME", ".")).resolve()
        config = _load_config_file(home)
        db_path = Path(str(_setting(config, "db_path", ".data/agent_memory.db")))
        export_dir = Path(str(_setting(config, "export_dir", "exports")))
        local_only = _parse_bool(_setting(config, "local_only", True), default=True)
        mcp_transport = str(_setting(config, "mcp_transport", "stdio")).strip().lower()
        mcp_host = str(_setting(config, "mcp_host", "127.0.0.1")).strip()
        mcp_port = int(_setting(config, "mcp_port", "8765"))
        embedding_dims = int(_setting(config, "embedding_dims", "256"))
        embedding_model = str(_setting(config, "embedding_model", "BAAI/bge-m3")).strip()
        reranker_model = str(_setting(config, "reranker_model", "BAAI/bge-reranker-base")).strip()
        vector_backend = str(_setting(config, "vector_backend", "auto")).strip().lower()
        approval_mode = str(_setting(config, "approval_mode", "manual")).strip().lower()
        owner_user_id = str(_setting(config, "owner_user_id", "local")).strip() or "local"
        workspace_id = str(_setting(config, "workspace_id", "local")).strip() or "local"
        project_id = str(_setting(config, "project_id", "default")).strip() or "default"
        visibility_scope = str(_setting(config, "visibility_scope", "private")).strip().lower()
        sensitivity_level = str(_setting(config, "sensitivity_level", "normal")).strip().lower()
        consensus_threshold = float(_setting(config, "consensus_threshold", "0.70"))
        max_review_rounds = int(_setting(config, "max_review_rounds", "5"))
        context_budget = int(_setting(config, "context_budget", "2500"))
        reranker_backend = str(_setting(config, "reranker_backend", "auto")).strip().lower()
        rerank_top_k = int(_setting(config, "rerank_top_k", "50"))
        rerank_max_chars = int(_setting(config, "rerank_max_chars", "1800"))

        if not db_path.is_absolute():
            db_path = (home / db_path).resolve()
        if not export_dir.is_absolute():
            export_dir = (home / export_dir).resolve()

        db_path.parent.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)

        if mcp_transport not in {"stdio", "sse"}:
            raise ValueError("AMO_MCP_TRANSPORT must be one of: stdio, sse")

        if local_only and mcp_transport == "sse":
            allowed_local_hosts = {"127.0.0.1", "localhost", "::1"}
            if mcp_host not in allowed_local_hosts:
                raise ValueError("AMO_LOCAL_ONLY=true requires AMO_MCP_HOST to be localhost")

        if embedding_dims <= 0:
            raise ValueError("AMO_EMBEDDING_DIMS must be a positive integer")
        if vector_backend not in {"auto", "faiss", "sqlite"}:
            raise ValueError("AMO_VECTOR_BACKEND must be one of: auto, faiss, sqlite")
        if reranker_backend not in {"auto", "lexical", "cross-encoder"}:
            raise ValueError("AMO_RERANKER_BACKEND must be one of: auto, lexical, cross-encoder")
        if approval_mode not in {"manual", "auto_safe"}:
            raise ValueError("AMO_APPROVAL_MODE must be one of: manual, auto_safe")
        if visibility_scope not in {"private", "project", "team", "public", "restricted"}:
            raise ValueError("AMO_VISIBILITY_SCOPE must be one of: private, project, team, public, restricted")
        if sensitivity_level not in {"low", "normal", "high", "secret"}:
            raise ValueError("AMO_SENSITIVITY_LEVEL must be one of: low, normal, high, secret")
        if not (0.0 <= consensus_threshold <= 1.0):
            raise ValueError("AMO_CONSENSUS_THRESHOLD must be between 0.0 and 1.0")
        if max_review_rounds <= 0:
            raise ValueError("AMO_MAX_REVIEW_ROUNDS must be a positive integer")
        if context_budget <= 0:
            raise ValueError("AMO_CONTEXT_BUDGET must be a positive integer")
        if rerank_top_k <= 0:
            raise ValueError("AMO_RERANK_TOP_K must be a positive integer")
        if rerank_max_chars <= 0:
            raise ValueError("AMO_RERANK_MAX_CHARS must be a positive integer")
        if not (1 <= mcp_port <= 65535):
            raise ValueError("AMO_MCP_PORT must be a valid TCP port")

        return cls(
            home=home,
            db_path=db_path,
            export_dir=export_dir,
            local_only=local_only,
            mcp_transport=mcp_transport,
            mcp_host=mcp_host,
            mcp_port=mcp_port,
            embedding_dims=embedding_dims,
            embedding_model=embedding_model,
            reranker_model=reranker_model,
            vector_backend=vector_backend,
            approval_mode=approval_mode,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            project_id=project_id,
            visibility_scope=visibility_scope,
            sensitivity_level=sensitivity_level,
            consensus_threshold=consensus_threshold,
            max_review_rounds=max_review_rounds,
            context_budget=context_budget,
            reranker_backend=reranker_backend,
            rerank_top_k=rerank_top_k,
            rerank_max_chars=rerank_max_chars,
        )
