from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_QWEN_MODEL = "qwen3.5:9b"


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
    payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
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
    graph_backend: str = "kuzu"
    graph_path: Path = Path(".graph/amo.kuzu")
    retrieval_db_path: Path = Path(".data/retrieval.sqlite")
    retrieval_graph_path: Path | None = None
    retrieval_graph_scope: str = ""
    evidence_dir: Path = Path(".evidence")
    qwen_runtime: str = "ollama"
    qwen_model: str = DEFAULT_QWEN_MODEL
    qwen_endpoint: str = "http://127.0.0.1:11434"
    qwen_timeout_seconds: float = 20.0
    qwen_planner_timeout_seconds: float = 8.0
    qwen_extract_timeout_seconds: float = 25.0
    qwen_compress_timeout_seconds: float = 12.0
    qwen_num_ctx: int = 2048
    drain_max_windows_per_run: int = 3
    auto_drain_enabled: bool = True
    auto_drain_interval_seconds: float = 8.0
    auto_drain_record_limit: int = 500
    auto_retrieval_node_limit: int = 10000
    auto_retrieval_max_doc_chars: int = 5000
    auto_embedding_batch_size: int = 10000
    peer_agent_enabled: bool = True
    peer_agent_runtime: str = "ollama"
    peer_agent_model: str = ""
    peer_agent_endpoint: str = ""
    peer_agent_timeout_seconds: float = 45.0
    peer_agent_answer_context_chars: int = 1050
    peer_agent_answer_retrieval_chars: int = 950
    peer_agent_answer_max_words: int = 90
    peer_agent_answer_num_predict: int = 180
    peer_agent_api_provider: str = ""
    peer_agent_api_base_url: str = ""
    peer_agent_api_model: str = ""
    peer_agent_api_key_env: str = ""
    peer_agent_allow_initiator_api_fallback: bool = True
    peer_agent_allow_retrieval_only_responses: bool = True
    peer_agent_min_confidence: float = 0.72
    peer_agent_strong_confidence: float = 0.80
    peer_agent_max_peers: int = 3
    peer_agent_room_timeout_seconds: float = 60.0
    peer_agent_summary_token_limit: int = 2500

    @classmethod
    def load(cls) -> "Settings":
        default_home = Path.home() / ".agent-memory-orchestrator"
        home = Path(os.getenv("AMO_HOME", str(default_home))).expanduser().resolve()
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
        graph_backend = str(_setting(config, "graph_backend", "kuzu")).strip().lower()
        graph_path = Path(str(_setting(config, "graph_path", ".graph/amo.kuzu")))
        retrieval_db_path = Path(str(_setting(config, "retrieval_db_path", ".data/retrieval.sqlite")))
        retrieval_graph_path_raw = str(_setting(config, "retrieval_graph_path", "")).strip()
        retrieval_graph_path = Path(retrieval_graph_path_raw) if retrieval_graph_path_raw else None
        retrieval_graph_scope = str(_setting(config, "retrieval_graph_scope", "")).strip()
        evidence_dir = Path(str(_setting(config, "evidence_dir", ".evidence")))
        qwen_runtime = str(_setting(config, "qwen_runtime", "ollama")).strip().lower()
        qwen_model = str(_setting(config, "qwen_model", DEFAULT_QWEN_MODEL)).strip()
        qwen_endpoint = str(_setting(config, "qwen_endpoint", "http://127.0.0.1:11434")).strip().rstrip("/")
        qwen_timeout_seconds = float(_setting(config, "qwen_timeout_seconds", "20"))
        qwen_planner_timeout_seconds = float(_setting(config, "qwen_planner_timeout_seconds", "8"))
        qwen_extract_timeout_seconds = float(_setting(config, "qwen_extract_timeout_seconds", "25"))
        qwen_compress_timeout_seconds = float(_setting(config, "qwen_compress_timeout_seconds", "12"))
        qwen_num_ctx = int(_setting(config, "qwen_num_ctx", "2048"))
        drain_max_windows_per_run = int(_setting(config, "drain_max_windows_per_run", "3"))
        auto_drain_enabled = _parse_bool(_setting(config, "auto_drain_enabled", True), default=True)
        auto_drain_interval_seconds = float(_setting(config, "auto_drain_interval_seconds", "8"))
        auto_drain_record_limit = int(_setting(config, "auto_drain_record_limit", "500"))
        auto_retrieval_node_limit = int(_setting(config, "auto_retrieval_node_limit", "10000"))
        auto_retrieval_max_doc_chars = int(_setting(config, "auto_retrieval_max_doc_chars", "5000"))
        auto_embedding_batch_size = int(_setting(config, "auto_embedding_batch_size", "10000"))
        peer_agent_enabled = _parse_bool(_setting(config, "peer_agent_enabled", True), default=True)
        peer_agent_runtime = str(_setting(config, "peer_agent_runtime", "ollama")).strip().lower()
        peer_agent_model = str(_setting(config, "peer_agent_model", qwen_model)).strip()
        peer_agent_endpoint = str(_setting(config, "peer_agent_endpoint", qwen_endpoint)).strip().rstrip("/")
        peer_agent_timeout_seconds = float(_setting(config, "peer_agent_timeout_seconds", "45"))
        peer_agent_answer_context_chars = int(_setting(config, "peer_agent_answer_context_chars", "1050"))
        peer_agent_answer_retrieval_chars = int(_setting(config, "peer_agent_answer_retrieval_chars", "950"))
        peer_agent_answer_max_words = int(_setting(config, "peer_agent_answer_max_words", "90"))
        peer_agent_answer_num_predict = int(_setting(config, "peer_agent_answer_num_predict", "180"))
        peer_agent_api_provider = str(_setting(config, "peer_agent_api_provider", "")).strip().lower()
        peer_agent_api_base_url = str(_setting(config, "peer_agent_api_base_url", "")).strip().rstrip("/")
        peer_agent_api_model = str(_setting(config, "peer_agent_api_model", "")).strip()
        peer_agent_api_key_env = str(_setting(config, "peer_agent_api_key_env", "")).strip()
        peer_agent_allow_initiator_api_fallback = _parse_bool(
            _setting(config, "peer_agent_allow_initiator_api_fallback", True),
            default=True,
        )
        peer_agent_allow_retrieval_only_responses = _parse_bool(
            _setting(config, "peer_agent_allow_retrieval_only_responses", True),
            default=True,
        )
        peer_agent_min_confidence = float(_setting(config, "peer_agent_min_confidence", "0.72"))
        peer_agent_strong_confidence = float(_setting(config, "peer_agent_strong_confidence", "0.80"))
        peer_agent_max_peers = int(_setting(config, "peer_agent_max_peers", "3"))
        peer_agent_room_timeout_seconds = float(_setting(config, "peer_agent_room_timeout_seconds", "60"))
        peer_agent_summary_token_limit = int(_setting(config, "peer_agent_summary_token_limit", "2500"))

        if not db_path.is_absolute():
            db_path = (home / db_path).resolve()
        if not export_dir.is_absolute():
            export_dir = (home / export_dir).resolve()
        if not graph_path.is_absolute():
            graph_path = (home / graph_path).resolve()
        if not retrieval_db_path.is_absolute():
            retrieval_db_path = (home / retrieval_db_path).resolve()
        if retrieval_graph_path is not None and not retrieval_graph_path.is_absolute():
            retrieval_graph_path = (home / retrieval_graph_path).resolve()
        if not evidence_dir.is_absolute():
            evidence_dir = (home / evidence_dir).resolve()

        db_path.parent.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_dir.mkdir(parents=True, exist_ok=True)

        if mcp_transport not in {"stdio", "sse"}:
            raise ValueError("AMO_MCP_TRANSPORT must be one of: stdio, sse")

        if local_only and mcp_transport == "sse":
            allowed_local_hosts = {"127.0.0.1", "localhost", "::1"}
            if mcp_host not in allowed_local_hosts:
                raise ValueError("AMO_LOCAL_ONLY=true requires AMO_MCP_HOST to be localhost")

        if embedding_dims <= 0:
            raise ValueError("AMO_EMBEDDING_DIMS must be a positive integer")
        if vector_backend not in {"auto", "faiss", "sqlite", "disabled"}:
            raise ValueError("AMO_VECTOR_BACKEND must be one of: auto, faiss, sqlite, disabled")
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
        if graph_backend != "kuzu":
            raise ValueError("AMO_GRAPH_BACKEND must be: kuzu")
        if qwen_runtime != "ollama":
            raise ValueError("AMO_QWEN_RUNTIME must be: ollama")
        if not qwen_model:
            raise ValueError("AMO_QWEN_MODEL is required")
        if not qwen_endpoint.startswith(("http://", "https://")):
            raise ValueError("AMO_QWEN_ENDPOINT must be an HTTP URL")
        if qwen_timeout_seconds <= 0:
            raise ValueError("AMO_QWEN_TIMEOUT_SECONDS must be positive")
        if min(qwen_planner_timeout_seconds, qwen_extract_timeout_seconds, qwen_compress_timeout_seconds) <= 0:
            raise ValueError("AMO_QWEN_*_TIMEOUT_SECONDS must be positive")
        if qwen_num_ctx <= 0:
            raise ValueError("AMO_QWEN_NUM_CTX must be positive")
        if peer_agent_runtime != "ollama":
            raise ValueError("AMO_PEER_AGENT_RUNTIME must be: ollama")
        if peer_agent_enabled and not peer_agent_model:
            raise ValueError("AMO_PEER_AGENT_MODEL is required when peer agent is enabled")
        if peer_agent_endpoint and not peer_agent_endpoint.startswith(("http://", "https://")):
            raise ValueError("AMO_PEER_AGENT_ENDPOINT must be an HTTP URL")
        if peer_agent_timeout_seconds <= 0:
            raise ValueError("AMO_PEER_AGENT_TIMEOUT_SECONDS must be positive")
        if min(
            peer_agent_answer_context_chars,
            peer_agent_answer_retrieval_chars,
            peer_agent_answer_max_words,
            peer_agent_answer_num_predict,
        ) <= 0:
            raise ValueError("AMO_PEER_AGENT_ANSWER_* budget settings must be positive")
        if peer_agent_api_provider not in {"", "openai_compatible"}:
            raise ValueError("AMO_PEER_AGENT_API_PROVIDER must be empty or openai_compatible")
        if peer_agent_api_provider and not peer_agent_api_base_url.startswith(("http://", "https://")):
            raise ValueError("AMO_PEER_AGENT_API_BASE_URL must be an HTTP URL")
        if peer_agent_api_provider and not peer_agent_api_model:
            raise ValueError("AMO_PEER_AGENT_API_MODEL is required when provider fallback is enabled")
        if peer_agent_api_provider and not peer_agent_api_key_env:
            raise ValueError("AMO_PEER_AGENT_API_KEY_ENV is required when provider fallback is enabled")
        if not (0.0 <= peer_agent_min_confidence <= 1.0):
            raise ValueError("AMO_PEER_AGENT_MIN_CONFIDENCE must be between 0.0 and 1.0")
        if not (0.0 <= peer_agent_strong_confidence <= 1.0):
            raise ValueError("AMO_PEER_AGENT_STRONG_CONFIDENCE must be between 0.0 and 1.0")
        if peer_agent_max_peers <= 0:
            raise ValueError("AMO_PEER_AGENT_MAX_PEERS must be positive")
        if peer_agent_room_timeout_seconds <= 0:
            raise ValueError("AMO_PEER_AGENT_ROOM_TIMEOUT_SECONDS must be positive")
        if peer_agent_summary_token_limit <= 0:
            raise ValueError("AMO_PEER_AGENT_SUMMARY_TOKEN_LIMIT must be positive")
        if drain_max_windows_per_run <= 0:
            raise ValueError("AMO_DRAIN_MAX_WINDOWS_PER_RUN must be positive")
        if auto_drain_interval_seconds <= 0:
            raise ValueError("AMO_AUTO_DRAIN_INTERVAL_SECONDS must be positive")
        if auto_drain_record_limit <= 0:
            raise ValueError("AMO_AUTO_DRAIN_RECORD_LIMIT must be positive")
        if auto_retrieval_node_limit <= 0:
            raise ValueError("AMO_AUTO_RETRIEVAL_NODE_LIMIT must be positive")
        if auto_retrieval_max_doc_chars <= 0:
            raise ValueError("AMO_AUTO_RETRIEVAL_MAX_DOC_CHARS must be positive")
        if auto_embedding_batch_size < 0:
            raise ValueError("AMO_AUTO_EMBEDDING_BATCH_SIZE must be zero or positive")

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
            graph_backend=graph_backend,
            graph_path=graph_path,
            retrieval_db_path=retrieval_db_path,
            retrieval_graph_path=retrieval_graph_path,
            retrieval_graph_scope=retrieval_graph_scope,
            evidence_dir=evidence_dir,
            qwen_runtime=qwen_runtime,
            qwen_model=qwen_model,
            qwen_endpoint=qwen_endpoint,
            qwen_timeout_seconds=qwen_timeout_seconds,
            qwen_planner_timeout_seconds=qwen_planner_timeout_seconds,
            qwen_extract_timeout_seconds=qwen_extract_timeout_seconds,
            qwen_compress_timeout_seconds=qwen_compress_timeout_seconds,
            qwen_num_ctx=qwen_num_ctx,
            drain_max_windows_per_run=drain_max_windows_per_run,
            auto_drain_enabled=auto_drain_enabled,
            auto_drain_interval_seconds=auto_drain_interval_seconds,
            auto_drain_record_limit=auto_drain_record_limit,
            auto_retrieval_node_limit=auto_retrieval_node_limit,
            auto_retrieval_max_doc_chars=auto_retrieval_max_doc_chars,
            auto_embedding_batch_size=auto_embedding_batch_size,
            peer_agent_enabled=peer_agent_enabled,
            peer_agent_runtime=peer_agent_runtime,
            peer_agent_model=peer_agent_model,
            peer_agent_endpoint=peer_agent_endpoint,
            peer_agent_timeout_seconds=peer_agent_timeout_seconds,
            peer_agent_answer_context_chars=peer_agent_answer_context_chars,
            peer_agent_answer_retrieval_chars=peer_agent_answer_retrieval_chars,
            peer_agent_answer_max_words=peer_agent_answer_max_words,
            peer_agent_answer_num_predict=peer_agent_answer_num_predict,
            peer_agent_api_provider=peer_agent_api_provider,
            peer_agent_api_base_url=peer_agent_api_base_url,
            peer_agent_api_model=peer_agent_api_model,
            peer_agent_api_key_env=peer_agent_api_key_env,
            peer_agent_allow_initiator_api_fallback=peer_agent_allow_initiator_api_fallback,
            peer_agent_allow_retrieval_only_responses=peer_agent_allow_retrieval_only_responses,
            peer_agent_min_confidence=peer_agent_min_confidence,
            peer_agent_strong_confidence=peer_agent_strong_confidence,
            peer_agent_max_peers=peer_agent_max_peers,
            peer_agent_room_timeout_seconds=peer_agent_room_timeout_seconds,
            peer_agent_summary_token_limit=peer_agent_summary_token_limit,
        )
