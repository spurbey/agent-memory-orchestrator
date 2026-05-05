from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentRole(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    USER = "user"
    SYSTEM = "system"


class OrchestrationState(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    REVISE = "revise"
    READY_FOR_USER = "ready_for_user"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(slots=True, frozen=True)
class Session:
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str


@dataclass(slots=True, frozen=True)
class Event:
    id: str
    session_id: str
    agent: str
    event_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    source_app: str = "unknown"
    owner_user_id: str = "local"
    workspace_id: str = "local"
    project_id: str = "default"
    visibility_scope: str = "private"
    sensitivity_level: str = "normal"
    redacted: bool = False


@dataclass(slots=True, frozen=True)
class Memory:
    id: str
    session_id: str
    source_event_id: str
    summary: str
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: str = ""


@dataclass(slots=True, frozen=True)
class Chunk:
    id: str
    session_id: str
    event_id: str
    chunk_index: int
    content_type: str
    text: str
    token_count: int
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass(slots=True, frozen=True)
class MemoryUnit:
    id: str
    session_id: str
    source_event_id: str
    source_chunk_id: str | None
    memory_type: str
    subject: str
    predicate: str
    object: str
    summary: str
    topic_key: str
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.4
    importance: float = 0.5
    status: str = "active"
    created_at: str = ""


@dataclass(slots=True, frozen=True)
class OrchestrationRound:
    id: str
    session_id: str
    round_index: int
    agent: str
    summary: str
    artifact_uri: str = ""
    blocking_issues: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""


@dataclass(slots=True, frozen=True)
class OrchestrationDecision:
    id: str
    session_id: str
    decision: str
    notes: str
    decided_by: str
    created_at: str
