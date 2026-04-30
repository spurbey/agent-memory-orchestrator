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
