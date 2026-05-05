from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


CONFIDENCE_BY_SIGNAL = {
    "explicit_user_decision": 0.95,
    "completed_fix": 0.90,
    "test_pass": 0.85,
    "tool_error": 0.80,
    "file_change": 0.70,
    "assistant_plan": 0.60,
    "vague": 0.40,
}

STOPWORDS = {
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "into",
    "your",
    "about",
    "there",
    "their",
    "would",
    "should",
    "could",
    "what",
    "when",
    "where",
    "which",
    "then",
    "than",
    "because",
}


@dataclass(slots=True, frozen=True)
class MemoryCandidate:
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
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_memory_candidates(
    text: str,
    *,
    content_type: str,
    event_type: str,
    agent: str,
    metadata: dict[str, Any] | None = None,
) -> list[MemoryCandidate]:
    clean = _compact(text)
    if not clean:
        return []

    entities = extract_entities(text, metadata or {})
    tags = extract_tags(clean)
    memory_type, signal = classify_memory_type(clean, content_type, event_type, agent)
    if memory_type == "none":
        return []

    subject = _choose_subject(entities, tags, agent)
    predicate = _predicate_for(memory_type)
    summary = _summarize(clean, memory_type, entities)
    topic_key = make_topic_key(subject, tags)
    confidence = confidence_for_signal(signal)
    importance = _importance(memory_type, confidence, len(clean))

    return [
        MemoryCandidate(
            memory_type=memory_type,
            subject=subject,
            predicate=predicate,
            object=summary,
            summary=summary,
            topic_key=topic_key,
            entities=entities,
            tags=tags,
            confidence=confidence,
            importance=importance,
            metadata={"signal": signal, "content_type": content_type},
        )
    ]


def classify_memory_type(text: str, content_type: str, event_type: str, agent: str) -> tuple[str, str]:
    lowered = text.lower()
    if agent == "user" and any(w in lowered for w in ("approved", "decided", "final decision", "go with")):
        return "decision", "explicit_user_decision"
    if event_type in {"decision", "approval"}:
        return "decision", "explicit_user_decision"
    if any(w in lowered for w in ("fixed", "resolved", "implemented", "added", "updated")):
        return "fix", "completed_fix"
    if any(w in lowered for w in ("passed", "all tests pass", "build succeeded", "green")):
        return "validation", "test_pass"
    if any(w in lowered for w in ("failed", "error", "exception", "traceback", "blocker", "bug")):
        return "bug", "tool_error"
    if content_type in {"diff", "code"}:
        return "file_change", "file_change"
    if any(w in lowered for w in ("plan", "propose", "should", "will", "architecture", "design")):
        return "decision", "assistant_plan"
    if len(text) >= 80:
        return "observation", "vague"
    return "none", "vague"


def confidence_for_signal(signal: str) -> float:
    return CONFIDENCE_BY_SIGNAL.get(signal, CONFIDENCE_BY_SIGNAL["vague"])


def extract_entities(text: str, metadata: dict[str, Any] | None = None) -> list[str]:
    meta = metadata or {}
    entities: list[str] = []
    for key in ("path", "file_path", "symbol"):
        value = str(meta.get(key) or "").strip()
        if value:
            entities.append(value)

    path_re = re.compile(r"(?<![\w/\\.-])[\w./\\-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|swift|dart|md|json|toml|yaml|yml)")
    symbol_re = re.compile(r"`([A-Za-z_][\w.:-]{2,})`")
    entities.extend(path_re.findall(text))
    entities.extend(symbol_re.findall(text))

    unique: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        normalized = entity.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            unique.append(normalized)
    return unique[:12]


def extract_tags(text: str, max_tags: int = 8) -> list[str]:
    words: list[str] = []
    for raw in text.lower().split():
        word = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})
        if len(word) < 4 or word in STOPWORDS:
            continue
        words.append(word)
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word not in seen:
            seen.add(word)
            unique.append(word)
        if len(unique) >= max_tags:
            break
    return unique


def make_topic_key(subject: str, tags: list[str]) -> str:
    base = subject if subject != "session" else "_".join(tags[:4])
    normalized = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
    return normalized or "general"


def _predicate_for(memory_type: str) -> str:
    return {
        "decision": "decides",
        "fix": "fixes",
        "validation": "validates",
        "bug": "reports",
        "file_change": "changes",
        "observation": "observes",
    }.get(memory_type, "observes")


def _choose_subject(entities: list[str], tags: list[str], agent: str) -> str:
    if entities:
        return entities[0]
    if tags:
        return tags[0]
    return agent or "session"


def _summarize(text: str, memory_type: str, entities: list[str], max_len: int = 320) -> str:
    prefix = memory_type.replace("_", " ").title()
    entity_note = f" [{', '.join(entities[:3])}]" if entities else ""
    body = text if len(text) <= max_len else text[: max_len - 3] + "..."
    return f"{prefix}{entity_note}: {body}"


def _importance(memory_type: str, confidence: float, content_len: int) -> float:
    base = {
        "decision": 0.8,
        "fix": 0.75,
        "validation": 0.7,
        "bug": 0.75,
        "file_change": 0.65,
        "observation": 0.45,
    }.get(memory_type, 0.4)
    length_bonus = min(0.1, content_len / 4000)
    return round(min(1.0, (base * 0.7) + (confidence * 0.2) + length_bonus), 3)


def _compact(text: str) -> str:
    return " ".join(text.split())
