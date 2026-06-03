from __future__ import annotations

MANAGED_BEGIN = "# BEGIN AMO MANAGED BLOCK"
MANAGED_END = "# END AMO MANAGED BLOCK"
SKILL_CHECKPOINT_MARKER = "AMO_SKILL_CHECKPOINT_MANAGED"
CLAUDE_MCP_NAME = "agent-memory-orchestrator"
CODEX_MCP_NAME = "agent_memory_orchestrator"
SUPPORTED_TARGETS = {"codex", "claude", "all"}

__all__ = [
    "CLAUDE_MCP_NAME",
    "CODEX_MCP_NAME",
    "MANAGED_BEGIN",
    "MANAGED_END",
    "SKILL_CHECKPOINT_MARKER",
    "SUPPORTED_TARGETS",
]
