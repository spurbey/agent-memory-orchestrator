from __future__ import annotations

import math
import os
import shutil
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..graph.store import GraphEdge, GraphNode, KuzuGraphStore
from .code_analysis import CodeNode, default_ast_expander, extract_code_nodes_from_commit
from .decision_extraction import extract_decisions
from .models import ExtractionRun, TimelineEvent
from .relationships import code_node_provenance_edges, produced_change_edges
from .chunking import build_decision_threads
from .timeline import TimelineGraph, build_timeline


DEFAULT_CODE_EMBEDDING_MODEL = "microsoft/codebert-base"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionGraphBuildOptions:
    session_id: str
    graph_path: Path
    repo_root: Path
    commit: str
    evidence_paths: tuple[Path, ...] = ()
    transcript_paths: tuple[Path, ...] = ()
    file_paths: tuple[str, ...] = ()
    text_embedding_model: str = "BAAI/bge-m3"
    code_embedding_model: str = DEFAULT_CODE_EMBEDDING_MODEL
    force: bool = False
    limit_events: int | None = None


@dataclass(frozen=True)
class SessionGraphQueryOptions:
    graph_path: Path
    query: str | None = None
    code_query: str | None = None
    text_embedding_model: str = "BAAI/bge-m3"
    code_embedding_model: str = DEFAULT_CODE_EMBEDDING_MODEL
    limit: int = 8


@dataclass
class SessionGraphBuildResult:
    ok: bool
    graph_path: str
    session_id: str
    extraction_run_id: str
    counts: dict[str, int] = field(default_factory=dict)
    ast_status_counts: dict[str, int] = field(default_factory=dict)
    edge_kinds: dict[str, int] = field(default_factory=dict)
    models: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class SessionGraphSearchHit:
    node_id: str
    kind: str
    label: str
    summary: str
    score: float
    evidence_id: str = ""
    commit_id: str = ""
    neighbors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionGraphQueryResult:
    ok: bool
    graph_path: str
    text_hits: list[SessionGraphSearchHit] = field(default_factory=list)
    code_hits: list[SessionGraphSearchHit] = field(default_factory=list)
    models: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


class StrictTextEmbedder:
    """Production text embedder. It fails loudly instead of producing fake vectors."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"text_embedding_runtime_unavailable:{exc}") from exc
        try:
            self._model = SentenceTransformer(model_name, local_files_only=True)
        except TypeError:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"text_embedding_model_unavailable:{model_name}:{exc}") from exc
        self._cache: dict[str, list[float]] = {}
        self.dims = _model_embedding_dimension(self._model)

    def embed(self, text: str) -> list[float]:
        text = text or ""
        if text in self._cache:
            return self._cache[text]
        vector = self._model.encode(text, normalize_embeddings=True)
        result = [float(x) for x in vector.tolist()]
        self._cache[text] = result
        return result

    def embed_many(self, texts: Iterable[str], *, batch_size: int = 16) -> None:
        missing = [text or "" for text in texts if (text or "") not in self._cache]
        if not missing:
            return
        unique = list(dict.fromkeys(missing))
        vectors = self._model.encode(unique, batch_size=batch_size, normalize_embeddings=True)
        for text, vector in zip(unique, vectors, strict=True):
            self._cache[text] = [float(x) for x in vector.tolist()]


def _model_embedding_dimension(model: Any) -> int:
    current = getattr(model, "get_embedding_dimension", None)
    if callable(current):
        return int(current() or 0)
    legacy = getattr(model, "get_sentence_embedding_dimension", None)
    if callable(legacy):
        return int(legacy() or 0)
    return 0


class CodeBertEmbedder:
    """Strict local CodeBERT embedder for code-node search."""

    def __init__(self, model_name: str = DEFAULT_CODE_EMBEDDING_MODEL) -> None:
        self.model_name = model_name
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"code_embedding_runtime_unavailable:{exc}") from exc
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
            self._model = AutoModel.from_pretrained(model_name, local_files_only=True)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(f"code_embedding_model_unavailable:{model_name}:{exc}") from exc
        self._torch = torch
        self._model.eval()
        self.dims = int(getattr(self._model.config, "hidden_size", 0) or 0)
        self._cache: dict[str, list[float]] = {}

    def embed(self, code: str) -> list[float]:
        code = code or ""
        if code in self._cache:
            return self._cache[code]
        self.embed_many([code], batch_size=1)
        return self._cache[code]

    def embed_many(self, snippets: Iterable[str], *, batch_size: int = 8) -> None:
        missing = [snippet or "" for snippet in snippets if (snippet or "") not in self._cache]
        if not missing:
            return
        unique = list(dict.fromkeys(missing))
        for offset in range(0, len(unique), max(1, batch_size)):
            batch = unique[offset : offset + batch_size]
            inputs = self._tokenizer(
                batch,
                max_length=384,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            with self._torch.no_grad():
                output = self._model(**inputs)
                mask = inputs["attention_mask"].unsqueeze(-1).expand(output.last_hidden_state.size()).float()
                pooled = (output.last_hidden_state * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                normed = self._torch.nn.functional.normalize(pooled, p=2, dim=1)
            for text, vector in zip(batch, normed, strict=True):
                self._cache[text] = [float(x) for x in vector.tolist()]


def build_session_graph(options: SessionGraphBuildOptions) -> SessionGraphBuildResult:
    graph_path = options.graph_path.resolve()
    if graph_path.exists():
        if not options.force:
            raise RuntimeError(f"graph_path_exists:{graph_path}")
        _remove_existing_graph(graph_path)
    graph_path.parent.mkdir(parents=True, exist_ok=True)

    text_embedder = StrictTextEmbedder(options.text_embedding_model)
    code_embedder = CodeBertEmbedder(options.code_embedding_model)

    timeline_graph = _build_real_timeline(options)
    evidence_ids = _timeline_evidence_ids(timeline_graph.events)
    extraction_run = replace(
        ExtractionRun.create(
            session_id=options.session_id,
            evidence_ids=evidence_ids,
            transcript_paths=tuple(str(path) for path in options.transcript_paths),
        ),
        algorithm_versions={
            "timeline": "production-real-v1",
            "chunking": "file-switch-explicit-bge-v1",
            "code_analysis": "git-unified0-python-ast-v1",
            "decision_extraction": "deterministic-patterns-v1",
            "relationships": "ranked-proximity-ast-v1",
        },
        model_versions={
            "text_embedding_model": options.text_embedding_model,
            "code_embedding_model": options.code_embedding_model,
        },
        thresholds={
            "semantic_drift": 0.65,
            "topic_revisit": 0.75,
            "ast_parent_max_hunk_multiple": 3.0,
        },
        status="complete",
    )

    thread_build = build_decision_threads(timeline_graph, extraction_run=extraction_run, embedder=text_embedder)
    threads = list(thread_build.threads)
    decisions = []
    for thread in threads:
        result = extract_decisions(thread=thread, events=list(timeline_graph.events), extraction_run=extraction_run)
        decisions.extend(result.decisions)
    code_nodes = _extract_real_code_nodes(options, extraction_run.id, evidence_ids)

    text_embedder.embed_many([thread.topic for thread in threads])
    text_embedder.embed_many([_node_text(decision) for decision in decisions])
    text_embedder.embed_many([_node_text(node) for node in code_nodes])
    code_embedder.embed_many([_code_embedding_text(node) for node in code_nodes])

    for node in code_nodes:
        node.metadata["code_embedding"] = code_embedder.embed(_code_embedding_text(node))
        node.metadata["text_embedding"] = text_embedder.embed(_node_text(node))
    for thread in threads:
        thread.metadata["text_embedding"] = text_embedder.embed(thread.topic)
    for decision in decisions:
        decision.metadata["text_embedding"] = text_embedder.embed(_node_text(decision))

    graph_nodes = _graph_nodes_from_session(
        extraction_run=extraction_run,
        threads=threads,
        decisions=decisions,
        code_nodes=code_nodes,
        commit=options.commit,
        repo_root=options.repo_root,
        file_paths=options.file_paths,
    )
    graph_edges = _graph_edges_from_session(
        extraction_run=extraction_run,
        threads=threads,
        decisions=decisions,
        code_nodes=code_nodes,
        commit=options.commit,
    )

    store = KuzuGraphStore(graph_path)
    store.init_schema()
    for node in graph_nodes:
        store.upsert_node(node)
    for edge in graph_edges:
        store.upsert_edge(edge)
    store.close()

    return SessionGraphBuildResult(
        ok=True,
        graph_path=str(graph_path),
        session_id=options.session_id,
        extraction_run_id=extraction_run.id,
        counts={
            "timeline_events": len(timeline_graph.events),
            "decision_threads": len(threads),
            "decisions": len(decisions),
            "code_nodes": len(code_nodes),
            "graph_nodes": len(graph_nodes),
            "graph_edges": len(graph_edges),
        },
        ast_status_counts=_count_ast_status(code_nodes),
        edge_kinds=_count_edge_kinds(graph_edges),
        models={
            "text_embedding_model": options.text_embedding_model,
            "text_embedding_dims": text_embedder.dims,
            "code_embedding_model": options.code_embedding_model,
            "code_embedding_dims": code_embedder.dims,
        },
        diagnostics=[
            *timeline_graph.diagnostics,
            *thread_build.diagnostics,
            *_build_diagnostics(list(timeline_graph.events), threads, decisions, code_nodes),
        ],
    )


def query_session_graph(options: SessionGraphQueryOptions) -> SessionGraphQueryResult:
    graph_path = options.graph_path.resolve()
    if not graph_path.exists():
        raise RuntimeError(f"graph_path_missing:{graph_path}")

    text_embedder: StrictTextEmbedder | None = None
    code_embedder: CodeBertEmbedder | None = None
    text_hits: list[SessionGraphSearchHit] = []
    code_hits: list[SessionGraphSearchHit] = []
    models: dict[str, Any] = {}

    store = KuzuGraphStore(graph_path)
    nodes = store.list_nodes(limit=10000)
    edges = store.list_edges(limit=10000)

    if options.query:
        text_embedder = StrictTextEmbedder(options.text_embedding_model)
        models["text_embedding_model"] = options.text_embedding_model
        models["text_embedding_dims"] = text_embedder.dims
        query_vector = text_embedder.embed(options.query)
        text_hits = _rank_nodes(
            nodes,
            edges,
            query_vector,
            embedding_key="text_embedding",
            limit=options.limit,
        )

    if options.code_query:
        code_embedder = CodeBertEmbedder(options.code_embedding_model)
        models["code_embedding_model"] = options.code_embedding_model
        models["code_embedding_dims"] = code_embedder.dims
        query_vector = code_embedder.embed(options.code_query)
        code_hits = _rank_nodes(
            [node for node in nodes if node.get("kind") == "CodeNode"],
            edges,
            query_vector,
            embedding_key="code_embedding",
            limit=options.limit,
        )

    store.close()
    return SessionGraphQueryResult(
        ok=True,
        graph_path=str(graph_path),
        text_hits=text_hits,
        code_hits=code_hits,
        models=models,
        diagnostics=[],
    )


def _build_real_timeline(options: SessionGraphBuildOptions) -> TimelineGraph:
    timeline_graph = build_timeline(
        session_id=options.session_id,
        evidence_paths=options.evidence_paths,
        transcript_paths=options.transcript_paths,
    )
    if options.limit_events is not None:
        timeline_graph = replace(timeline_graph, events=timeline_graph.events[: options.limit_events])
    if not timeline_graph.events:
        raise RuntimeError(f"no_timeline_events:{options.session_id}")
    return timeline_graph


def _extract_real_code_nodes(
    options: SessionGraphBuildOptions,
    extraction_run_id: str,
    evidence_ids: tuple[str, ...],
) -> list[CodeNode]:
    if not options.file_paths:
        raise RuntimeError("file_paths_required_for_code_node_extraction")
    nodes: list[CodeNode] = []
    for file_path in options.file_paths:
        _, extracted = extract_code_nodes_from_commit(
            repo_root=options.repo_root,
            commit=options.commit,
            session_id=options.session_id,
            extraction_run_id=extraction_run_id,
            evidence_ids=evidence_ids,
            file_path=file_path,
            ast_expander=default_ast_expander,
        )
        nodes.extend(extracted)
    return nodes


def _graph_nodes_from_session(
    *,
    extraction_run: ExtractionRun,
    threads: Iterable[Any],
    decisions: Iterable[Any],
    code_nodes: Iterable[CodeNode],
    commit: str,
    repo_root: Path,
    file_paths: Iterable[str],
) -> list[GraphNode]:
    nodes: list[GraphNode] = [_extraction_run_graph_node(extraction_run)]
    nodes.extend(_thread_graph_node(thread, extraction_run) for thread in threads)
    nodes.extend(_decision_graph_node(decision, extraction_run, commit) for decision in decisions)
    nodes.extend(_code_graph_node(node, extraction_run) for node in code_nodes)
    nodes.append(
        GraphNode(
            id=f"commit:{commit}",
            kind="GitCommit",
            label=commit[:12],
            summary=f"Git commit {commit[:12]} linked to session graph extraction",
            status="committed",
            scope="central",
            session_id=extraction_run.session_id,
            project_id="default",
            source_app="codex",
            evidence_id=extraction_run.evidence_ids[-1] if extraction_run.evidence_ids else "",
            commit_id=commit,
            created_at=now_utc(),
            updated_at=now_utc(),
            metadata={"repo_root": str(repo_root)},
        )
    )
    for file_path in sorted(set(file_paths)):
        nodes.append(
            GraphNode(
                id=f"file:{file_path}",
                kind="File",
                label=Path(file_path).name,
                summary=f"File touched by session code extraction: {file_path}",
                status="active",
                scope="session",
                session_id=extraction_run.session_id,
                project_id="default",
                source_app="codex",
                evidence_id=extraction_run.evidence_ids[-1] if extraction_run.evidence_ids else "",
                commit_id=commit,
                created_at=now_utc(),
                updated_at=now_utc(),
                metadata={"path": file_path},
            )
        )
    return nodes


def _graph_edges_from_session(
    *,
    extraction_run: ExtractionRun,
    threads: Iterable[Any],
    decisions: Iterable[Any],
    code_nodes: list[CodeNode],
    commit: str,
) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    for thread in threads:
        edges.append(
            GraphEdge(
                id=f"edge:{extraction_run.id}:has_thread:{thread.id}",
                source_id=extraction_run.id,
                target_id=thread.id,
                kind="HAS_THREAD",
                weight=0.8,
                created_at=now_utc(),
                metadata={"extraction_run_id": extraction_run.id},
            )
        )
    for decision in decisions:
        edges.append(
            GraphEdge(
                id=f"edge:{extraction_run.id}:extracted_decision:{decision.id}",
                source_id=extraction_run.id,
                target_id=decision.id,
                kind="EXTRACTED_DECISION",
                weight=0.85,
                created_at=now_utc(),
                metadata={"extraction_run_id": extraction_run.id},
            )
        )
    for reasoning_edge in code_node_provenance_edges(extraction_run_id=extraction_run.id, code_nodes=code_nodes):
        edges.append(_graph_edge_from_reasoning(reasoning_edge))
    for thread in threads:
        thread_decisions = [decision for decision in decisions if decision.metadata.get("thread_id") == thread.id]
        for reasoning_edge in produced_change_edges(decisions=thread_decisions, code_nodes=code_nodes, thread=thread):
            edges.append(_graph_edge_from_reasoning(reasoning_edge))
    for node in code_nodes:
        edges.append(
            GraphEdge(
                id=f"edge:{_safe_edge_part(node.id)}:modifies:{_safe_edge_part(node.file_path)}",
                source_id=node.id,
                target_id=f"file:{node.file_path}",
                kind="MODIFIES",
                weight=0.7,
                created_at=now_utc(),
                metadata={
                    "file_path": node.file_path,
                    "extraction_run_id": extraction_run.id,
                },
            )
        )
        edges.append(
            GraphEdge(
                id=f"edge:{_safe_edge_part(node.id)}:linked_to_commit:{_safe_edge_part(commit)}",
                source_id=node.id,
                target_id=f"commit:{commit}",
                kind="LINKED_TO_COMMIT",
                weight=0.8,
                created_at=now_utc(),
                metadata={
                    "commit_id": commit,
                    "extraction_run_id": extraction_run.id,
                },
            )
        )
    return edges


def _rank_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    query_vector: list[float],
    *,
    embedding_key: str,
    limit: int,
) -> list[SessionGraphSearchHit]:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        vector = metadata.get(embedding_key)
        if not isinstance(vector, list):
            continue
        score = _cosine(query_vector, [float(x) for x in vector])
        if score > 0:
            ranked.append((score, node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        SessionGraphSearchHit(
            node_id=str(node.get("id") or ""),
            kind=str(node.get("kind") or ""),
            label=str(node.get("label") or ""),
            summary=str(node.get("summary") or ""),
            score=round(score, 6),
            evidence_id=str(node.get("evidence_id") or ""),
            commit_id=str(node.get("commit_id") or ""),
            neighbors=_neighbors_for(str(node.get("id") or ""), nodes, edges),
        )
        for score, node in ranked[:limit]
    ]


def _neighbors_for(node_id: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(node.get("id") or ""): node for node in nodes}
    result: list[dict[str, Any]] = []
    for edge in edges:
        other_id = ""
        direction = ""
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id == node_id:
            other_id = target_id
            direction = "out"
        elif target_id == node_id:
            other_id = source_id
            direction = "in"
        else:
            continue
        other = by_id.get(other_id)
        result.append(
            {
                "direction": direction,
                "edge_kind": edge.get("kind") or "",
                "node_id": other_id,
                "node_kind": other.get("kind") if other else "",
                "label": other.get("label") if other else "",
            }
        )
    return result[:20]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _remove_existing_graph(path: Path) -> None:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    amo_home = Path(os.environ.get("AMO_HOME", str(Path.home() / ".agent-memory-orchestrator"))).resolve()
    if not _is_within(resolved, cwd) and not _is_within(resolved, amo_home):
        raise RuntimeError(f"refuse_to_delete_graph_outside_workspace_or_amo_home:{resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()


def _timeline_evidence_ids(timeline: Iterable[TimelineEvent]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for event in timeline:
        evidence_id = event.evidence_id
        if evidence_id and evidence_id not in seen:
            seen.add(evidence_id)
            values.append(evidence_id)
    return tuple(values)


def _node_text(node: Any) -> str:
    return " | ".join(
        str(value)
        for value in [
            getattr(node, "label", ""),
            getattr(node, "summary", ""),
            getattr(node, "file_path", ""),
            getattr(node, "metadata", {}).get("symbol_name", "") if isinstance(getattr(node, "metadata", {}), dict) else "",
        ]
        if value
    )


def _code_embedding_text(node: CodeNode) -> str:
    return "\n".join(
        value
        for value in [
            f"# file: {node.file_path}",
            f"# symbol: {node.metadata.get('symbol_name') or ''}",
            node.content,
        ]
        if value
    )


def _count_ast_status(code_nodes: Iterable[CodeNode]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in code_nodes:
        counts[node.ast_status] = counts.get(node.ast_status, 0) + 1
    return counts


def _count_edge_kinds(edges: Iterable[GraphEdge]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges:
        counts[edge.kind] = counts.get(edge.kind, 0) + 1
    return counts


def _build_diagnostics(
    timeline: list[TimelineEvent],
    threads: list[Any],
    decisions: list[Any],
    code_nodes: list[CodeNode],
) -> list[str]:
    diagnostics: list[str] = []
    if not any(event.event_type == "agent_message" for event in timeline):
        diagnostics.append("timeline_missing_agent_messages")
    if not decisions:
        diagnostics.append("no_decisions_extracted")
    if not code_nodes:
        diagnostics.append("no_code_nodes_extracted")
    if not any(node.ast_status == "parsed" for node in code_nodes):
        diagnostics.append("no_ast_parsed_code_nodes")
    if threads and len(decisions) < max(1, len(threads) // 100):
        diagnostics.append("decision_extraction_sparse_for_thread_count")
    return diagnostics


def _extraction_run_graph_node(extraction_run: ExtractionRun) -> GraphNode:
    return GraphNode(
        id=extraction_run.id,
        kind="ExtractionRun",
        label=extraction_run.id,
        summary=f"Extraction run for session {extraction_run.session_id}",
        status=extraction_run.status,
        scope="session",
        session_id=extraction_run.session_id,
        project_id="default",
        source_app="codex",
        evidence_id=extraction_run.evidence_ids[-1] if extraction_run.evidence_ids else "",
        created_at=extraction_run.created_at or now_utc(),
        updated_at=now_utc(),
        metadata={
            "evidence_ids": list(extraction_run.evidence_ids),
            "transcript_paths": list(extraction_run.transcript_paths),
            "algorithm_versions": extraction_run.algorithm_versions,
            "model_versions": extraction_run.model_versions,
            "thresholds": extraction_run.thresholds,
            "diagnostics": list(extraction_run.diagnostics),
        },
    )


def _thread_graph_node(thread: Any, extraction_run: ExtractionRun) -> GraphNode:
    return GraphNode(
        id=thread.id,
        kind="DecisionThread",
        label=thread.topic[:96] or thread.id,
        summary=thread.topic,
        status=thread.status,
        scope="session",
        session_id=thread.session_id,
        project_id="default",
        source_app="codex",
        evidence_id=thread.evidence_ids[-1] if thread.evidence_ids else (extraction_run.evidence_ids[-1] if extraction_run.evidence_ids else ""),
        created_at=now_utc(),
        updated_at=now_utc(),
        metadata={
            **thread.metadata,
            "event_ids": list(thread.event_ids),
            "file_paths": list(thread.file_paths),
            "evidence_ids": list(thread.evidence_ids),
            "extraction_run_id": thread.extraction_run_id,
            "source": thread.source,
            "confidence": thread.confidence,
        },
    )


def _decision_graph_node(decision: Any, extraction_run: ExtractionRun, commit: str) -> GraphNode:
    return GraphNode(
        id=decision.id,
        kind=decision.kind,
        label=decision.summary[:96] or decision.id,
        summary=decision.summary,
        status=decision.status,
        scope="session",
        session_id=decision.session_id,
        project_id="default",
        source_app="codex",
        evidence_id=decision.evidence_ids[-1] if decision.evidence_ids else (extraction_run.evidence_ids[-1] if extraction_run.evidence_ids else ""),
        commit_id=commit,
        created_at=now_utc(),
        updated_at=now_utc(),
        metadata={
            **decision.metadata,
            "evidence_ids": list(decision.evidence_ids),
            "extraction_run_id": decision.extraction_run_id,
            "source": decision.source,
            "confidence": decision.confidence,
            "qwen_call": decision.qwen_call,
        },
    )


def _code_graph_node(code_node: CodeNode, extraction_run: ExtractionRun) -> GraphNode:
    symbol = str(code_node.metadata.get("symbol_name") or "")
    label_parts = [Path(code_node.file_path).name, f"{code_node.line_start}-{code_node.line_end}"]
    if symbol:
        label_parts.append(symbol)
    return GraphNode(
        id=code_node.id,
        kind="CodeNode",
        label=":".join(label_parts),
        summary=_code_summary(code_node),
        status=code_node.status,
        scope="session",
        session_id=code_node.session_id,
        project_id="default",
        source_app="codex",
        evidence_id=code_node.evidence_ids[-1] if code_node.evidence_ids else (extraction_run.evidence_ids[-1] if extraction_run.evidence_ids else ""),
        commit_id=code_node.commit_id,
        created_at=now_utc(),
        updated_at=now_utc(),
        metadata={
            **code_node.metadata,
            "file_path": code_node.file_path,
            "ast_type": code_node.ast_type,
            "line_start": code_node.line_start,
            "line_end": code_node.line_end,
            "content": code_node.content,
            "prev_content": code_node.prev_content,
            "prev_content_present": bool(code_node.prev_content),
            "evidence_ids": list(code_node.evidence_ids),
            "extraction_run_id": code_node.extraction_run_id,
            "source": code_node.source,
            "confidence": code_node.confidence,
        },
    )


def _code_summary(code_node: CodeNode) -> str:
    symbol = str(code_node.metadata.get("symbol_name") or "").strip()
    subject = f"{code_node.ast_type} {symbol}".strip()
    snippet = " ".join(line.strip() for line in code_node.content.splitlines() if line.strip())[:240]
    return f"{subject} in {code_node.file_path}:{code_node.line_start}-{code_node.line_end}. {snippet}".strip()


def _graph_edge_from_reasoning(edge: Any) -> GraphEdge:
    evidence_ids = tuple(edge.evidence_ids or ())
    return GraphEdge(
        id=f"edge:{_safe_edge_part(edge.source_id)}:{_safe_edge_part(edge.kind)}:{_safe_edge_part(edge.target_id)}",
        source_id=edge.source_id,
        target_id=edge.target_id,
        kind=edge.kind,
        weight=float(edge.confidence or 0.8),
        confidence=float(edge.confidence or 0.8),
        evidence_id=evidence_ids[-1] if evidence_ids else "",
        created_at=now_utc(),
        metadata={**(edge.metadata or {}), "evidence_ids": list(evidence_ids)},
    )


def _safe_edge_part(value: str) -> str:
    keep = []
    for char in value:
        if char.isalnum() or char in {"_", "-", "."}:
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep)[:160]


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def build_and_query_session_graph(
    build_options: SessionGraphBuildOptions,
    *,
    query: str | None = None,
    code_query: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    build_result = build_session_graph(build_options)
    query_result = query_session_graph(
        SessionGraphQueryOptions(
            graph_path=build_options.graph_path,
            query=query,
            code_query=code_query,
            text_embedding_model=build_options.text_embedding_model,
            code_embedding_model=build_options.code_embedding_model,
            limit=limit,
        )
    )
    return {
        "ok": build_result.ok and query_result.ok,
        "build": asdict(build_result),
        "query": asdict(query_result),
    }


def default_session_graph_path(session_id: str) -> Path:
    safe_session = _safe_edge_part(session_id)
    return Path(os.environ.get("AMO_HOME", str(Path.home() / ".agent-memory-orchestrator"))) / ".graph" / "sessions" / f"{safe_session}.kuzu"
