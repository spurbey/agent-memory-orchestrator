from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import Settings
from .evidence_window import clean_evidence_window
from .graph_store import GraphEdge, GraphNode, GraphStore
from .graph_triggers import TriggerDecision
from .qwen_client import OllamaQwenClient, QwenUnavailable
from .raw_evidence import RawEvidenceRef
from .versioning import VersionBackend


ANSWER_GRADE_KINDS = {"Decision", "WorkChange", "Bug", "Fix", "Blocker", "TestRun", "ContextSnapshot"}


@dataclass(slots=True, frozen=True)
class GraphDelta:
    summary: str
    goal: str = ""
    latest_decision: str = ""
    changed_files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_step: str = ""
    decisions: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    bugs: list[str] = field(default_factory=list)

    def as_context_metadata(self, evidence_ids: list[str], trigger: TriggerDecision) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "latest_decision": self.latest_decision,
            "changed_files": self.changed_files,
            "tests": self.tests,
            "blockers": self.blockers,
            "next_step": self.next_step,
            "evidence_ids": evidence_ids,
            "trigger": trigger.as_dict(),
        }


class GraphExtractor(Protocol):
    def extract(self, *, session_id: str, records: list[dict[str, Any]], trigger: TriggerDecision) -> GraphDelta:
        """Extract a session graph delta from a bounded evidence window."""


class DeterministicGraphExtractor:
    """Small local fallback for tests and when Qwen is unavailable."""

    def extract(self, *, session_id: str, records: list[dict[str, Any]], trigger: TriggerDecision) -> GraphDelta:
        clean_records = clean_evidence_window(records, trigger)
        text = "\n".join(_clean_record_text(record) for record in clean_records)
        files = _clean_changed_files(clean_records) or _extract_files(text)
        tests = [line for line in _important_lines(text) if _looks_like_test(line)]
        decisions = [line for line in _important_lines(text) if "decision" in line.lower() or "decide" in line.lower()]
        fixes = [line for line in _important_lines(text) if "fix" in line.lower() or "fixed" in line.lower()]
        bugs = [line for line in _important_lines(text) if "bug" in line.lower() or "error" in line.lower()]
        summary = _trim(_clean_summary(clean_records) or f"{trigger.trigger_type} update in session {session_id}", 700)
        return GraphDelta(
            summary=summary,
            goal=_trim(_first_clean_goal(clean_records) or _first_prompt(records), 240),
            latest_decision=_trim(decisions[-1], 280) if decisions else "",
            changed_files=files,
            tests=tests[:5],
            blockers=[line for line in _important_lines(text) if "blocker" in line.lower()][:5],
            next_step="Review graph context and continue from the latest work change.",
            decisions=decisions[:6],
            fixes=fixes[:6],
            bugs=bugs[:6],
        )


class QwenGraphExtractor:
    """Ollama/Qwen extractor for bounded write/test/git evidence windows.

    The daemon owns this path. Hooks never instantiate it.
    """

    def __init__(self, settings: Settings, *, fallback: GraphExtractor | None = None, timeout_seconds: float = 30.0) -> None:
        self.settings = settings
        self.client = OllamaQwenClient(
            endpoint=settings.qwen_endpoint,
            model=settings.qwen_model,
            timeout_seconds=min(timeout_seconds, settings.qwen_timeout_seconds),
        )
        self.fallback = fallback or DeterministicGraphExtractor()

    def extract(self, *, session_id: str, records: list[dict[str, Any]], trigger: TriggerDecision) -> GraphDelta:
        cleaned_evidence = clean_evidence_window(records, trigger)
        prompt = (
            "/no_think\n"
            "Extract an AMO session GraphDelta from this bounded evidence window. "
            "Return only JSON with keys: summary, goal, latest_decision, changed_files, "
            "tests, blockers, next_step, decisions, fixes, bugs. "
            "All list fields must be arrays of strings. Keep summary under 700 chars. "
            "Only include durable work memory caused by this trigger; ignore ordinary read-only chat.\n"
            f"session_id={session_id}\n"
            f"trigger={json.dumps(trigger.as_dict(), ensure_ascii=False, sort_keys=True)}\n"
            f"evidence={json.dumps(cleaned_evidence, ensure_ascii=False, indent=2)}"
        )
        try:
            payload = self.client._generate_json(prompt, num_predict=900)  # noqa: SLF001 - package-local Ollama adapter.
        except QwenUnavailable:
            return self.fallback.extract(session_id=session_id, records=records, trigger=trigger)
        return GraphDelta(
            summary=_string_field(payload.get("summary"), limit=700)
            or self.fallback.extract(session_id=session_id, records=records, trigger=trigger).summary,
            goal=_string_field(payload.get("goal"), limit=240),
            latest_decision=_string_field(payload.get("latest_decision"), limit=280),
            changed_files=_string_list(payload.get("changed_files"), limit=20),
            tests=_string_list(payload.get("tests"), limit=8),
            blockers=_string_list(payload.get("blockers"), limit=8),
            next_step=_string_field(payload.get("next_step"), limit=240),
            decisions=_string_list(payload.get("decisions"), limit=8),
            fixes=_string_list(payload.get("fixes"), limit=8),
            bugs=_string_list(payload.get("bugs"), limit=8),
        )


class SessionGraphBuilder:
    """Builds session draft graph nodes from raw evidence windows."""

    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
        version_backend: VersionBackend,
        extractor: GraphExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.version_backend = version_backend
        self.extractor = extractor or DeterministicGraphExtractor()

    def ingest_basic_record(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = _payload(record)
        session_id = str(record.get("session_id") or payload.get("session_id") or payload.get("sessionId") or "default")
        source_app = str(record.get("source_app") or payload.get("source_app") or "unknown")
        event_name = _event_name(record)
        evidence = _evidence_ref(record)
        cwd = payload.get("cwd") or payload.get("repo_root")
        git = _compact_git(self.version_backend.snapshot(cwd).as_dict())

        session_node = GraphNode(
            id=f"session:{session_id}",
            kind="Session",
            label=session_id,
            summary=f"{source_app} session {session_id}",
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            metadata={"git": git},
        )
        app_node = GraphNode(
            id=f"app:{source_app}",
            kind="App",
            label=source_app,
            summary=f"Source app {source_app}",
            status="active",
            scope="central",
            source_app=source_app,
        )
        evidence_node = GraphNode(
            id=f"evidence:{evidence.id}",
            kind="RawEvidenceRef",
            label=evidence.id,
            summary=f"{event_name} raw evidence from {source_app}",
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=evidence.id,
            metadata=evidence.as_dict(),
        )
        event_node = GraphNode(
            id=f"event:{evidence.id}",
            kind=_node_kind_for_event(event_name),
            label=_trim(_record_content(record), 96) or event_name,
            summary=_trim(f"{event_name}: {_record_content(record)}", 500),
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=evidence.id,
            metadata={"event_type": event_name, "git": git},
        )
        for node in (session_node, app_node, evidence_node, event_node):
            self.store.upsert_node(node)
        self._edge(session_node.id, app_node.id, "PART_OF", evidence.id)
        self._edge(session_node.id, event_node.id, "HAS_TURN", evidence.id)
        self._edge(event_node.id, evidence_node.id, "EVIDENCED_BY", evidence.id)
        self._upsert_git_nodes(session_id=session_id, source_app=source_app, evidence_id=evidence.id, git=git)
        return {"session_id": session_id, "event_id": event_node.id, "evidence_id": evidence.id, "git": git}

    def process_window(self, *, session_id: str, records: list[dict[str, Any]], trigger: TriggerDecision) -> dict[str, Any]:
        if not records:
            return {"processed": False, "reason": "empty_window", "nodes": [], "edges": []}
        cleaned_evidence = clean_evidence_window(records, trigger)
        delta = self.extractor.extract(session_id=session_id, records=records, trigger=trigger)
        evidence_ids = [str(record.get("id") or "") for record in records if record.get("id")]
        source_app = str(records[-1].get("source_app") or "unknown")
        trigger_evidence = evidence_ids[-1] if evidence_ids else ""
        now_id = uuid.uuid4().hex
        window_id = f"window:{session_id}:{trigger_evidence or now_id}"
        delta_id = f"delta:{session_id}:{trigger_evidence or now_id}"
        work_id = f"work:{session_id}:{now_id}"
        context_id = f"context:{session_id}:latest"

        window_node = GraphNode(
            id=window_id,
            kind="CleanedEvidenceWindow",
            label=f"{trigger.trigger_type} cleaned window",
            summary=_trim(_cleaned_window_summary(cleaned_evidence) or f"Cleaned {trigger.trigger_type} evidence window", 700),
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=trigger_evidence,
            metadata={
                "trigger": trigger.as_dict(),
                "evidence_ids": evidence_ids,
                "cleaned_evidence": cleaned_evidence,
                "record_count": len(records),
                "cleaned_count": len(cleaned_evidence),
            },
        )
        delta_node = GraphNode(
            id=delta_id,
            kind="GraphDelta",
            label=_trim(delta.summary, 120) or f"{trigger.trigger_type} graph delta",
            summary=delta.summary,
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=trigger_evidence,
            metadata={
                **delta.as_context_metadata(evidence_ids, trigger),
                "decisions": delta.decisions,
                "fixes": delta.fixes,
                "bugs": delta.bugs,
            },
        )
        work_node = GraphNode(
            id=work_id,
            kind="WorkChange",
            label=_trim(delta.summary, 120),
            summary=delta.summary,
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=trigger_evidence,
            metadata={"trigger": trigger.as_dict(), "evidence_ids": evidence_ids},
        )
        context_node = GraphNode(
            id=context_id,
            kind="ContextSnapshot",
            label=f"latest context for {session_id}",
            summary=delta.summary,
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=trigger_evidence,
            metadata=delta.as_context_metadata(evidence_ids, trigger),
        )
        self.store.upsert_node(window_node)
        self.store.upsert_node(delta_node)
        self.store.upsert_node(work_node)
        self.store.upsert_node(context_node)
        self._edge(f"session:{session_id}", window_id, "HAS_WINDOW", trigger_evidence)
        for evidence_id in evidence_ids:
            self._edge(f"evidence:{evidence_id}", window_id, "CLEANED_INTO", evidence_id)
            self._edge(f"event:{evidence_id}", window_id, "CLEANED_INTO", evidence_id)
        self._edge(window_id, delta_id, "EXTRACTED_AS", trigger_evidence)
        self._edge(delta_id, work_id, "CREATED", trigger_evidence)
        self._edge(delta_id, context_id, "CREATED", trigger_evidence)
        self._edge(f"session:{session_id}", work_id, "PRODUCED", trigger_evidence)
        self._edge(work_id, context_id, "REFINES", trigger_evidence)

        nodes = [window_node.as_dict(), delta_node.as_dict(), work_node.as_dict(), context_node.as_dict()]
        for file_path in delta.changed_files:
            file_node = GraphNode(
                id=f"file:{file_path}",
                kind="File",
                label=file_path,
                summary=f"File touched by AMO work ledger: {file_path}",
                status="active",
                scope="central",
                project_id=self.settings.project_id,
                source_app=source_app,
            )
            self.store.upsert_node(file_node)
            self._edge(delta_id, file_node.id, "CREATED", trigger_evidence)
            self._edge(work_id, file_node.id, "MODIFIES", trigger_evidence)
            nodes.append(file_node.as_dict())
        for text in delta.decisions or ([delta.latest_decision] if delta.latest_decision else []):
            node = self._answer_node("Decision", session_id, source_app, text, trigger_evidence)
            self.store.upsert_node(node)
            self._edge(delta_id, node.id, "CREATED", trigger_evidence)
            self._edge(work_id, node.id, "IMPLEMENTS", trigger_evidence)
            nodes.append(node.as_dict())
        for text in delta.fixes:
            node = self._answer_node("Fix", session_id, source_app, text, trigger_evidence)
            self.store.upsert_node(node)
            self._edge(delta_id, node.id, "CREATED", trigger_evidence)
            self._edge(work_id, node.id, "FIXES", trigger_evidence)
            nodes.append(node.as_dict())
        for text in delta.bugs:
            node = self._answer_node("Bug", session_id, source_app, text, trigger_evidence)
            self.store.upsert_node(node)
            self._edge(delta_id, node.id, "CREATED", trigger_evidence)
            self._edge(work_id, node.id, "ABOUT", trigger_evidence)
            nodes.append(node.as_dict())
        for text in delta.tests:
            node = self._answer_node("TestRun", session_id, source_app, text, trigger_evidence)
            self.store.upsert_node(node)
            self._edge(delta_id, node.id, "CREATED", trigger_evidence)
            self._edge(node.id, work_id, "VALIDATED_BY", trigger_evidence)
            nodes.append(node.as_dict())

        commit = _extract_commit(records)
        if not commit and trigger.is_commit:
            cwd = _payload(records[-1]).get("cwd") or _payload(records[-1]).get("repo_root")
            snapshot = self.version_backend.snapshot(cwd)
            commit = snapshot.head if snapshot.available else ""
        if commit:
            commit_node = GraphNode(
                id=f"commit:{commit}",
                kind="GitCommit",
                label=commit[:12],
                summary=f"Git commit {commit[:12]} linked to session work",
                status="committed",
                scope="central",
                session_id=session_id,
                project_id=self.settings.project_id,
                source_app=source_app,
                evidence_id=trigger_evidence,
                commit_id=commit,
            )
            self.store.upsert_node(commit_node)
            self._edge(work_id, commit_node.id, "COMMITTED_AS", trigger_evidence)
            self._edge(f"session:{session_id}", commit_node.id, "MERGED_INTO", trigger_evidence)
            nodes.append(commit_node.as_dict())

        return {
            "processed": True,
            "session_id": session_id,
            "trigger": trigger.as_dict(),
            "context_node_id": context_id,
            "work_node_id": work_id,
            "nodes": nodes,
            "evidence_ids": evidence_ids,
        }

    def _answer_node(self, kind: str, session_id: str, source_app: str, text: str, evidence_id: str) -> GraphNode:
        return GraphNode(
            id=f"{kind.lower()}:{session_id}:{uuid.uuid4().hex}",
            kind=kind,
            label=_trim(text, 120),
            summary=_trim(text, 700),
            status="draft",
            scope="session",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app=source_app,
            evidence_id=evidence_id,
        )

    def _upsert_git_nodes(self, *, session_id: str, source_app: str, evidence_id: str, git: dict[str, Any]) -> None:
        if not git.get("available"):
            return
        repo_root = str(git.get("repo_root") or "")
        branch = str(git.get("branch") or "")
        repo_id = f"repo:{repo_root}"
        branch_id = f"branch:{repo_root}:{branch}"
        self.store.upsert_node(
            GraphNode(
                id=repo_id,
                kind="Repo",
                label=repo_root,
                summary=f"Local Git repo {repo_root}",
                status="active",
                scope="central",
                project_id=self.settings.project_id,
                source_app=source_app,
                metadata=git,
            )
        )
        self.store.upsert_node(
            GraphNode(
                id=branch_id,
                kind="Branch",
                label=branch,
                summary=f"Branch {branch} in {repo_root}",
                status="active",
                scope="central",
                session_id=session_id,
                project_id=self.settings.project_id,
                source_app=source_app,
                commit_id=str(git.get("head") or ""),
                metadata=git,
            )
        )
        self._edge(f"session:{session_id}", repo_id, "PART_OF", evidence_id)
        self._edge(branch_id, repo_id, "PART_OF", evidence_id)

    def _edge(self, source: str, target: str, kind: str, evidence_id: str) -> None:
        self.store.upsert_edge(
            GraphEdge(
                id=f"edge:{source}:{kind}:{target}",
                source_id=source,
                target_id=target,
                kind=kind,
                evidence_id=evidence_id,
            )
        )


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _event_name(record: dict[str, Any]) -> str:
    payload = _payload(record)
    raw = str(record.get("event_name") or payload.get("hook_event_name") or "message")
    return _snake(raw)


def _evidence_ref(record: dict[str, Any]) -> RawEvidenceRef:
    return RawEvidenceRef(
        id=str(record.get("id") or f"raw_{uuid.uuid4().hex}"),
        hash=str(record.get("hash") or ""),
        path=str(record.get("path") or ""),
        offset=int(record.get("offset") or 0),
        session_id=str(record.get("session_id") or _payload(record).get("session_id") or "default"),
        source_app=str(record.get("source_app") or _payload(record).get("source_app") or "unknown"),
        event_name=str(record.get("event_name") or _payload(record).get("hook_event_name") or "message"),
        created_at=str(record.get("created_at") or datetime.now(timezone.utc).isoformat()),
    )


def _node_kind_for_event(event_type: str) -> str:
    if event_type in {"prompt", "user_prompt_submit"}:
        return "Prompt"
    if "tool" in event_type:
        return "ToolResult"
    if "response" in event_type:
        return "Response"
    return "Turn"


def _record_content(record: dict[str, Any]) -> str:
    payload = _payload(record)
    if payload.get("continue") is True and payload.get("captureOnly") is True:
        return _trim(str(payload.get("note") or "AMO hook capture response"), 400)
    if str(payload.get("hook_event_name") or "").lower() == "stop":
        return _trim(str(payload.get("last_assistant_message") or "session stop"), 800)
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input")
    if tool_name or isinstance(tool_input, dict):
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        response = _tool_response_summary(str(payload.get("tool_response") or payload.get("content") or ""))
        return _trim(f"tool={tool_name} command={command} response={response}", 1200)
    for key in ("prompt", "content", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = payload.get("tool_input")
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _records_for_qwen(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trigger = TriggerDecision(True, "manual_clean", "manual clean evidence window")
    return clean_evidence_window(records, trigger)


def _clean_record_text(record: dict[str, Any]) -> str:
    parts: list[str] = [
        str(record.get("kind") or ""),
        str(record.get("summary") or ""),
        str(record.get("command") or ""),
    ]
    for key in ("changed_files", "tests", "commits"):
        value = record.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return " ".join(part for part in parts if part)


def _clean_changed_files(records: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for record in records:
        files = record.get("changed_files")
        if not isinstance(files, list):
            continue
        for file_path in files:
            text = str(file_path or "")
            if text and text not in seen:
                seen.append(text)
    return seen[:20]


def _clean_summary(records: list[dict[str, Any]]) -> str:
    summaries = [
        str(record.get("summary") or "")
        for record in records
        if record.get("kind") not in {"user_goal"} and record.get("summary")
    ]
    return " ".join(summaries)


def _cleaned_window_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    parts: list[str] = []
    for record in records:
        kind = str(record.get("kind") or "")
        summary = _trim(str(record.get("summary") or ""), 160)
        if kind and summary:
            parts.append(f"{kind}: {summary}")
        elif summary:
            parts.append(summary)
    return _trim(" | ".join(parts), 700)


def _first_clean_goal(records: list[dict[str, Any]]) -> str:
    for record in records:
        if record.get("kind") == "user_goal" and record.get("summary"):
            return str(record["summary"])
    return ""


def _first_prompt(records: list[dict[str, Any]]) -> str:
    for record in records:
        payload = _payload(record)
        prompt = payload.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return ""


def _important_lines(text: str) -> list[str]:
    lines = [" ".join(line.strip().split()) for line in text.splitlines()]
    return [_trim(line, 300) for line in lines if len(line) > 8][:24]


def _extract_files(text: str) -> list[str]:
    matches = re.findall(r"(?i)(?:[\w.-]+[\\/])*[\w.-]+\.(?:py|js|ts|tsx|jsx|md|toml|json|yaml|yml|css|html|rs|go)", text)
    seen: list[str] = []
    for match in matches:
        clean = match.strip().replace("\\", "/")
        if clean not in seen:
            seen.append(clean)
    return seen[:20]


def _extract_commit(records: list[dict[str, Any]]) -> str:
    text = "\n".join(_record_content(record) for record in records)
    full = re.search(r"\b[0-9a-f]{40}\b", text, re.IGNORECASE)
    if full:
        return full.group(0).lower()
    short = re.search(r"\[[^\]]+ ([0-9a-f]{7,})\]", text, re.IGNORECASE)
    return short.group(1).lower() if short else ""


def _looks_like_test(line: str) -> bool:
    lowered = line.lower()
    return any(term in lowered for term in ("pytest", "ruff check", "tests passed", "passed", "failed"))


def _tool_response_summary(response: str) -> str:
    lines = [" ".join(line.strip().split()) for line in response.splitlines() if line.strip()]
    important = [
        line
        for line in lines
        if any(
            term in line.lower()
            for term in (
                "passed",
                "failed",
                "error",
                "warning",
                "all checks passed",
                "files changed",
                "commit",
                "decision",
                "fix",
            )
        )
    ]
    selected = important[:8] or lines[:4]
    return _trim(" | ".join(selected), 800)


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def _trim(value: str, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _string_field(value: Any, *, limit: int) -> str:
    if isinstance(value, list):
        text = " ".join(str(item or "").strip() for item in value if str(item or "").strip())
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "")
    return _trim(text, limit)


def _compact_git(git: dict[str, Any]) -> dict[str, Any]:
    changed = [str(path) for path in git.get("changed_files", []) if path]
    staged = [str(path) for path in git.get("staged_files", []) if path]
    return {
        "available": bool(git.get("available")),
        "repo_root": str(git.get("repo_root") or ""),
        "branch": str(git.get("branch") or ""),
        "head": str(git.get("head") or ""),
        "dirty": bool(git.get("dirty")),
        "changed_count": len(changed),
        "staged_count": len(staged),
        "changed_files": changed[:20],
        "staged_files": staged[:20],
        "error": str(git.get("error") or ""),
    }


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _trim(str(item or ""), 400)
        if text:
            rows.append(text)
    return rows[:limit]
