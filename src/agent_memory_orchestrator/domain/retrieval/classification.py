from __future__ import annotations

import re


def classify_query(query: str) -> str:
    lowered = query.lower()
    if re.search(r"\b(version flow|version history|version chain|symbol version|symbol history|show versions?|over time|evolved?|evolution)\b", lowered):
        return "version_flow"
    if re.search(r"\bwhat changed\b|\bchanges? for\b|\bhow .* changed\b", lowered):
        return "code_why" if query_has_code_locator(query) or re.search(r"\b(code|file|function|class|module|ui|graph|service|controls?)\b", lowered) else "semantic_search"
    if "why" in lowered or "reason" in lowered:
        return (
            "code_why"
            if query_has_code_locator(query) or re.search(r"\b(code|file|function|class|module|ui|service|controls?)\b", lowered)
            else "decision_history"
        )
    if "decision" in lowered or "decide" in lowered:
        return "decision_history"
    if "::" in query or re.search(r"\b[\w./-]+\.(py|js|ts|tsx|jsx|md)\b", lowered):
        return "version_flow"
    return "semantic_search"


def query_has_code_locator(query: str) -> bool:
    lowered = query.lower()
    return bool(
        "::" in query
        or re.search(r"\b[\w./-]+\.(py|js|ts|tsx|jsx|md|toml|json|yaml|yml)\b", lowered)
        or re.search(r"\b[a-z0-9]+_[a-z0-9_]+\b", lowered)
    )


__all__ = ["classify_query", "query_has_code_locator"]
