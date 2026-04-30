from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    consensus_threshold: float
    max_review_rounds: int

    @classmethod
    def load(cls) -> "Settings":
        home = Path(os.getenv("AMO_HOME", ".")).resolve()
        db_path = Path(os.getenv("AMO_DB_PATH", ".data/agent_memory.db"))
        export_dir = Path(os.getenv("AMO_EXPORT_DIR", "exports"))
        local_only = _parse_bool(os.getenv("AMO_LOCAL_ONLY"), default=True)
        mcp_transport = os.getenv("AMO_MCP_TRANSPORT", "stdio").strip().lower()
        mcp_host = os.getenv("AMO_MCP_HOST", "127.0.0.1").strip()
        mcp_port = int(os.getenv("AMO_MCP_PORT", "8765"))
        embedding_dims = int(os.getenv("AMO_EMBEDDING_DIMS", "256"))
        consensus_threshold = float(os.getenv("AMO_CONSENSUS_THRESHOLD", "0.70"))
        max_review_rounds = int(os.getenv("AMO_MAX_REVIEW_ROUNDS", "5"))

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
        if not (0.0 <= consensus_threshold <= 1.0):
            raise ValueError("AMO_CONSENSUS_THRESHOLD must be between 0.0 and 1.0")
        if max_review_rounds <= 0:
            raise ValueError("AMO_MAX_REVIEW_ROUNDS must be a positive integer")
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
            consensus_threshold=consensus_threshold,
            max_review_rounds=max_review_rounds,
        )
