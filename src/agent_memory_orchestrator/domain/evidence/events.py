from __future__ import annotations

HOOK_CONTEXT_EVENTS = {"session_start"}
CAPTURE_ONLY_EVENTS = {"user_prompt_submit", "prompt", "post_tool_use", "tool_result", "stop", "session_stop"}

__all__ = ["CAPTURE_ONLY_EVENTS", "HOOK_CONTEXT_EVENTS"]
