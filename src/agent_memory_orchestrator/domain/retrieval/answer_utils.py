from __future__ import annotations

import re
from typing import Any
from typing import Iterable


def _answer_code_locator_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or "")):
        lowered = token.lower().replace("\\", "/")
        if "_" in lowered or "::" in lowered or "/" in lowered or "." in lowered:
            terms.add(lowered)
            terms.update(part for part in re.split(r"[^a-zA-Z0-9_]+", lowered) if len(part) > 2)
    return terms


def _normalize_public_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _public_answer_text(text: str) -> str:
    cleaned = re.sub(r"\{[^{}]{0,2000}\}", "", str(text or ""))
    cleaned = re.sub(r"\{.*$", "", cleaned)
    cleaned = re.sub(r"\b(?:FileImpactSummary|CodeImpactSummary|ReasoningNode):\s*", "", cleaned)
    cleaned = re.sub(r"\bImpact summary for\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bWP\d{3,}\b", "work item", cleaned)
    cleaned = re.sub(r"\bE\d{3,}\b", "evidence record", cleaned)
    cleaned = re.sub(r"\bpacket\s+work item\b", "work item", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwork packet\b", "work item", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bevidence\s+evidence record\b", "evidence record", cleaned, flags=re.IGNORECASE)
    return cleaned


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)
            return
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for value in values:
        visit(value)
    return out


def _unique_public_values(values: Iterable[Any]) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return _unique_nonempty([values])
    return _unique_nonempty(values)


def _body_field(body: str, field: str) -> str:
    prefix = f"{field.strip().lower()}:"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[-1].strip()
    return ""


def _best_answer_line(body: str) -> str:
    for prefix in ("statement:", "summary:", "reason:", "symbol:", "file_path:"):
        for line in body.splitlines():
            if line.strip().lower().startswith(prefix):
                return line.split(":", 1)[-1].strip()
    return body.strip().splitlines()[0][:300] if body.strip() else ""


__all__ = [
    "_answer_code_locator_terms",
    "_best_answer_line",
    "_body_field",
    "_normalize_public_path",
    "_public_answer_text",
    "_unique_nonempty",
    "_unique_public_values",
]
