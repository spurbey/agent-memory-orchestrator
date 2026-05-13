from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Protocol

from ..llm.embeddings import embed_text
from .models import DecisionThread
from .models import ExtractionRun
from .models import TimelineEvent
from .timeline import TimelineGraph


EXPLICIT_TRANSITION_RE = re.compile(
    r"\b(now let me|moving on|next issue|that(?:'s| is) fixed|actually let me check|return(?:ing)? to|back to)\b",
    re.IGNORECASE,
)

LOW_VALUE_EVENT_TYPES = {"session_start", "stop"}


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        """Return a normalized or comparable embedding for text."""


@dataclass(slots=True, frozen=True)
class HashEmbeddingProvider:
    dims: int = 64

    def embed(self, text: str) -> list[float]:
        return embed_text(text, self.dims)


@dataclass(slots=True, frozen=True)
class ChunkingConfig:
    semantic_window_size: int = 3
    semantic_drift_threshold: float = 0.65
    revisit_threshold: float = 0.75


@dataclass(slots=True, frozen=True)
class Chunk:
    id: str
    events: tuple[TimelineEvent, ...]
    files: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        values: list[str] = []
        for event in self.events:
            if not event.content:
                continue
            if event.event_type in {"agent_message", "user_message"}:
                values.append(event.content)
                continue
            if event.metadata.get("tool_fact"):
                values.append(event.content)
        if values:
            return "\n".join(values)
        return "\n".join(event.content for event in self.events if event.content)


@dataclass(slots=True, frozen=True)
class DecisionThreadBuild:
    extraction_run: ExtractionRun
    chunks: tuple[Chunk, ...]
    threads: tuple[DecisionThread, ...]
    diagnostics: tuple[str, ...] = ()


def build_decision_threads(
    timeline: TimelineGraph,
    *,
    extraction_run: ExtractionRun,
    embedder: EmbeddingProvider | None = None,
    config: ChunkingConfig | None = None,
) -> DecisionThreadBuild:
    cfg = config or ChunkingConfig()
    diagnostics: list[str] = []
    _prewarm_embeddings(embedder, _semantic_window_texts(timeline.events, cfg))
    chunks = _build_chunks(timeline.events, embedder=embedder, config=cfg, diagnostics=diagnostics)
    _prewarm_embeddings(embedder, [text for chunk in chunks for text in (chunk.text, _topic_label(chunk)) if text])
    threads = _chunks_to_threads(
        chunks,
        session_id=timeline.session_id,
        extraction_run_id=extraction_run.id,
        embedder=embedder,
        config=cfg,
        diagnostics=diagnostics,
    )
    return DecisionThreadBuild(
        extraction_run=extraction_run,
        chunks=tuple(chunks),
        threads=tuple(threads),
        diagnostics=tuple(diagnostics),
    )


def semantic_drift_boundary(
    previous_messages: list[str],
    next_message: str,
    *,
    embedder: EmbeddingProvider | None,
    config: ChunkingConfig | None = None,
) -> tuple[bool, float | None, str]:
    cfg = config or ChunkingConfig()
    if len(previous_messages) < cfg.semantic_window_size:
        return False, None, "insufficient_window"
    if embedder is None:
        return False, None, "embedding_status=missing"
    window_a = "\n".join(previous_messages[-cfg.semantic_window_size :])
    window_b = "\n".join([*previous_messages[-(cfg.semantic_window_size - 1) :], next_message])
    similarity = cosine_similarity(embedder.embed(window_a), embedder.embed(window_b))
    return similarity < cfg.semantic_drift_threshold, similarity, "semantic_drift"


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _build_chunks(
    events: tuple[TimelineEvent, ...],
    *,
    embedder: EmbeddingProvider | None,
    config: ChunkingConfig,
    diagnostics: list[str],
) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_events: list[TimelineEvent] = []
    current_files: set[str] = set()
    assistant_messages: list[str] = []

    for event in events:
        event_files = _meaningful_files(event.files)
        boundary_reasons: list[str] = []
        if current_events and event.event_type not in LOW_VALUE_EVENT_TYPES:
            if _is_file_switch(current_files, event_files):
                boundary_reasons.append("file_switch")
            if event.event_type == "agent_message" and EXPLICIT_TRANSITION_RE.search(event.content):
                boundary_reasons.append("explicit_transition")
            if event.event_type == "agent_message":
                drift, score, reason = semantic_drift_boundary(
                    assistant_messages,
                    event.content,
                    embedder=embedder,
                    config=config,
                )
                if reason == "embedding_status=missing" and reason not in diagnostics:
                    diagnostics.append(reason)
                if drift:
                    boundary_reasons.append(f"semantic_drift:{score:.3f}" if score is not None else "semantic_drift")

        if boundary_reasons:
            chunks.append(_make_chunk(len(chunks), current_events, current_files, diagnostics=boundary_reasons))
            current_events = []
            current_files = set()
            assistant_messages = []

        current_events.append(event)
        current_files.update(event_files)
        if event.event_type == "agent_message" and event.content:
            assistant_messages.append(event.content)

    if current_events:
        chunks.append(_make_chunk(len(chunks), current_events, current_files, diagnostics=()))
    return chunks


def _chunks_to_threads(
    chunks: list[Chunk],
    *,
    session_id: str,
    extraction_run_id: str,
    embedder: EmbeddingProvider | None,
    config: ChunkingConfig,
    diagnostics: list[str],
) -> list[DecisionThread]:
    threads: list[DecisionThread] = []
    for chunk in chunks:
        merged = False
        for index, prior in enumerate(threads):
            file_overlap = set(chunk.files).intersection(prior.file_paths)
            if not file_overlap:
                continue
            score = _topic_similarity(chunk.text, prior.topic, embedder)
            if score is None:
                diagnostics.append("revisit_embedding_status=missing")
                continue
            if score >= config.revisit_threshold:
                threads[index] = replace(
                    prior,
                    event_ids=(*prior.event_ids, *(event.id for event in chunk.events)),
                    file_paths=tuple(sorted(set(prior.file_paths).union(chunk.files))),
                    evidence_ids=tuple(sorted(set(prior.evidence_ids).union(_chunk_evidence_ids(chunk)))),
                    metadata={
                        **prior.metadata,
                        "continued_chunk_count": int(prior.metadata.get("continued_chunk_count", 0)) + 1,
                        "last_revisit_similarity": round(score, 6),
                    },
                )
                merged = True
                break
        if merged:
            continue
        topic = _topic_label(chunk)
        threads.append(
            DecisionThread(
                id=f"thread:{session_id}:{len(threads) + 1}",
                session_id=session_id,
                extraction_run_id=extraction_run_id,
                event_ids=tuple(event.id for event in chunk.events),
                topic=topic,
                file_paths=chunk.files,
                evidence_ids=_chunk_evidence_ids(chunk),
                metadata={"chunk_id": chunk.id, "chunk_diagnostics": list(chunk.diagnostics)},
            )
        )
    return threads


def _topic_similarity(left: str, right: str, embedder: EmbeddingProvider | None) -> float | None:
    if embedder is None:
        return None
    return cosine_similarity(embedder.embed(left), embedder.embed(right))


def _prewarm_embeddings(embedder: EmbeddingProvider | None, texts: list[str]) -> None:
    if embedder is None:
        return
    embed_many = getattr(embedder, "embed_many", None)
    if not callable(embed_many):
        return
    unique = list(dict.fromkeys(text for text in texts if text))
    if unique:
        embed_many(unique)


def _semantic_window_texts(events: tuple[TimelineEvent, ...], config: ChunkingConfig) -> list[str]:
    assistant_messages: list[str] = []
    windows: list[str] = []
    for event in events:
        if event.event_type != "agent_message" or not event.content:
            continue
        if len(assistant_messages) >= config.semantic_window_size:
            windows.append("\n".join(assistant_messages[-config.semantic_window_size :]))
            windows.append("\n".join([*assistant_messages[-(config.semantic_window_size - 1) :], event.content]))
        assistant_messages.append(event.content)
    return windows


def _make_chunk(index: int, events: list[TimelineEvent], files: set[str], *, diagnostics: list[str] | tuple[str, ...]) -> Chunk:
    return Chunk(
        id=f"chunk:{index + 1}",
        events=tuple(events),
        files=tuple(sorted(files)),
        diagnostics=tuple(diagnostics),
    )


def _is_file_switch(current_files: set[str], event_files: tuple[str, ...]) -> bool:
    if not current_files or not event_files:
        return False
    current = {_compare_path(path) for path in current_files if _compare_path(path)}
    incoming = {_compare_path(path) for path in event_files if _compare_path(path)}
    if not current or not incoming:
        return False
    if current.intersection(incoming):
        return False
    for left in current:
        for right in incoming:
            if _related_paths(left, right) or _same_parent(left, right):
                return False
    return True


def _meaningful_files(files: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for file_path in files:
        normalized = _display_path(file_path)
        if not normalized or normalized.lower() in {"c:", "m", "a"}:
            continue
        if len(normalized) <= 2 and normalized.endswith(":"):
            continue
        out.append(normalized)
    return tuple(out)


def _chunk_evidence_ids(chunk: Chunk) -> tuple[str, ...]:
    return tuple(event.evidence_id for event in chunk.events if event.evidence_id)


def _topic_label(chunk: Chunk) -> str:
    files = ", ".join(chunk.files[:3])
    if files:
        return files
    for event in chunk.events:
        if event.event_type in {"agent_message", "user_message"} and event.content:
            return event.content.strip().replace("\n", " ")[:120]
    return chunk.id


def _display_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip().strip('"')
    normalized = re.sub(r"/+", "/", normalized)
    return normalized


def _compare_path(value: str) -> str:
    normalized = _display_path(value).lower().rstrip("/")
    marker = "/agent-memory-orchestrator/"
    if marker in normalized:
        return "agent-memory-orchestrator/" + normalized.split(marker, 1)[1]
    if normalized.endswith("/agent-memory-orchestrator"):
        return "agent-memory-orchestrator"
    return normalized


def _related_paths(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _same_parent(left: str, right: str) -> bool:
    left_path = left.rsplit("/", 1)
    right_path = right.rsplit("/", 1)
    if len(left_path) != 2 or len(right_path) != 2:
        return False
    left_parent, left_name = left_path
    right_parent, right_name = right_path
    if left_parent != right_parent or left_name == right_name:
        return False
    if left_parent in {"", ".", "agent-memory-orchestrator"}:
        return False
    if "agent_memory_orchestrator" not in left_parent:
        return False
    return "." in left_name and "." in right_name
