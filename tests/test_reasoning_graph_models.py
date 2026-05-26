from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_memory_orchestrator.reasoning_graph import CodeNode
from agent_memory_orchestrator.reasoning_graph import ChunkingConfig
from agent_memory_orchestrator.reasoning_graph import code_node_commit_edges
from agent_memory_orchestrator.reasoning_graph import DecisionThread
from agent_memory_orchestrator.reasoning_graph import extract_decisions
from agent_memory_orchestrator.reasoning_graph import DecisionUnit
from agent_memory_orchestrator.reasoning_graph import ExtractionRun
from agent_memory_orchestrator.reasoning_graph import HashEmbeddingProvider
from agent_memory_orchestrator.reasoning_graph import TestRun as GraphTestRun
from agent_memory_orchestrator.reasoning_graph import TimelineGraph
from agent_memory_orchestrator.reasoning_graph import TimelineEvent
from agent_memory_orchestrator.reasoning_graph import build_timeline
from agent_memory_orchestrator.reasoning_graph import build_decision_threads
from agent_memory_orchestrator.reasoning_graph import load_codex_transcript_events
from agent_memory_orchestrator.reasoning_graph import code_node_provenance_edges
from agent_memory_orchestrator.reasoning_graph import code_nodes_from_hunks
from agent_memory_orchestrator.reasoning_graph import default_ast_expander
from agent_memory_orchestrator.reasoning_graph import extract_code_nodes_from_commit
from agent_memory_orchestrator.reasoning_graph import parse_unified_zero_hunks
from agent_memory_orchestrator.reasoning_graph import produced_change_edges
from agent_memory_orchestrator.reasoning_graph import python_ast_expander
from agent_memory_orchestrator.reasoning_graph import query_session_graph
from agent_memory_orchestrator.reasoning_graph import resolve_code_node_version
from agent_memory_orchestrator.reasoning_graph import semantic_drift_boundary
from agent_memory_orchestrator.reasoning_graph import should_accept_ast_parent
from agent_memory_orchestrator.reasoning_graph import validation_edges_for_test
from agent_memory_orchestrator.reasoning_graph import validate_graph_object
from agent_memory_orchestrator.reasoning_graph import validate_reasoning_edge
from agent_memory_orchestrator.reasoning_graph import validate_status_transition
from agent_memory_orchestrator.reasoning_graph import work_change_code_edges
from agent_memory_orchestrator.reasoning_graph import work_change_commit_edges
from agent_memory_orchestrator.reasoning_graph import work_changes_from_commit_windows


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


class _KeywordEmbedder:
    def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if "dashboard" in lowered or "css" in lowered:
            return [0.0, 1.0]
        if "api" in lowered or "client" in lowered:
            return [0.2, 0.8]
        return [1.0, 0.0]


class _QwenDecisionStub:
    def __init__(self, payload):
        self.payload = payload

    def extract(self, payload):
        self.last_input = payload
        return self.payload


class _SessionQueryStore:
    def __init__(self, nodes: list[dict], neighbors: dict[str, list[dict]] | None = None) -> None:
        self.nodes = nodes
        self._neighbors = neighbors or {}

    def list_nodes(self, *, limit: int = 25, kinds: list[str] | None = None, session_id: str = "", status: str = "") -> list[dict]:
        out = []
        for node in self.nodes:
            if kinds and node.get("kind") not in kinds:
                continue
            if session_id and node.get("session_id") != session_id:
                continue
            if status and node.get("status") != status:
                continue
            out.append(node)
        return out[:limit]

    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict]:
        return self._neighbors.get(node_id, [])[:limit]


def _event(event_id: str, event_type: str, content: str, *, files: tuple[str, ...] = ()) -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        session_id="s1",
        event_type=event_type,
        timestamp=f"2026-05-08T00:00:0{event_id[-1]}.000Z",
        content=content,
        files=files,
    )


def _thread(thread_id: str, topic: str, *, files: tuple[str, ...], event_ids: tuple[str, ...] | None = None) -> DecisionThread:
    return DecisionThread(
        id=thread_id,
        session_id="s1",
        extraction_run_id="run1",
        event_ids=event_ids or (f"event:{thread_id}",),
        topic=topic,
        file_paths=files,
        evidence_ids=("raw1",),
    )


def _code_node(node_id: str, content: str, *, line_start: int = 10, line_end: int = 12) -> CodeNode:
    return CodeNode(
        id=node_id,
        session_id="s1",
        extraction_run_id="run1",
        file_path="src/agent_memory_orchestrator/install_service.py",
        ast_type="function_definition",
        line_start=line_start,
        line_end=line_end,
        content=content,
        commit_id="c5326f8",
        evidence_ids=("raw1",),
    )


def test_rejects_answer_grade_node_without_evidence_ids() -> None:
    node = DecisionUnit(
        id="decision:s1:missing-evidence",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw1",
        summary="Use typed graph validators before Kuzu writes.",
        evidence_ids=(),
    )

    report = validate_graph_object(node)

    assert report.ok is False
    assert "answer_grade_missing_evidence" in _codes(report)


def test_rejects_qwen_derived_node_without_extraction_run_id() -> None:
    node = DecisionUnit(
        id="decision:s1:qwen",
        session_id="s1",
        extraction_run_id="",
        summary="Qwen extracted a decision.",
        evidence_ids=("raw1",),
        source="qwen",
        qwen_call="decision_extraction_fallback",
        confidence=0.9,
    )

    report = validate_graph_object(node)

    assert report.ok is False
    assert "qwen_missing_extraction_run" in _codes(report)
    assert "answer_grade_missing_extraction_run" in _codes(report)


def test_rejects_low_confidence_qwen_output() -> None:
    node = DecisionUnit(
        id="decision:s1:qwen-low",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw1",
        summary="Qwen extracted a low confidence decision.",
        evidence_ids=("raw1",),
        source="qwen",
        qwen_call="decision_extraction_fallback",
        confidence=0.4,
    )

    report = validate_graph_object(node)

    assert report.ok is False
    assert "qwen_low_confidence" in _codes(report)


def test_rejects_invalid_status_transition() -> None:
    report = validate_status_transition("committed", "draft")

    assert report.ok is False
    assert "invalid_status_transition" in _codes(report)


def test_accepts_valid_code_node_with_provenance() -> None:
    node = CodeNode(
        id="code:s1:install-service:1",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw1",
        file_path="src/agent_memory_orchestrator/install_service.py",
        ast_type="function_definition",
        line_start=10,
        line_end=22,
        content="def apply_install_plan():\n    pass",
        commit_id="c5326f8",
        evidence_ids=("raw1",),
    )

    report = validate_graph_object(node)

    assert report.ok is True


def test_accepts_work_change_with_commit_provenance() -> None:
    change = DecisionUnit(
        id="work:s1:abc123",
        session_id="s1",
        extraction_run_id="run1",
        summary="feat(memory): add local memory engine. Changed 2 files.",
        evidence_ids=("commit:abc123",),
        kind="WorkChange",
        confidence=1.0,
    )

    report = validate_graph_object(change)

    assert report.ok is True


def test_extraction_run_requires_evidence_ids() -> None:
    run = ExtractionRun.create(session_id="s1", evidence_ids=())

    report = validate_graph_object(run)

    assert report.ok is False
    assert "extraction_run_missing_evidence" in _codes(report)


def test_real_evidence_record_builds_timeline_event_without_graph_mutation() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    if not evidence_path.exists():
        pytest.skip("real AMO evidence file is not available in this environment")

    target = "raw_639e2963e72e4e3bb063042eeb221afd"
    raw = None
    with evidence_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("id") == target:
                raw = item
                break
    if raw is None:
        pytest.skip(f"selected real evidence id {target} is not available")

    event = TimelineEvent.from_raw_evidence(raw)

    assert event.session_id == "019e08eb-8f1f-7381-8f25-59344c4ac8a9"
    assert event.evidence_id == target
    assert event.event_type == "post_tool_use"
    assert event.tool_name == "apply_patch"
    assert "install_service.py" in "\n".join(event.files)
    assert event.transcript_path.endswith("019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl")


def test_transcript_imports_assistant_message(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:00.000Z",
                        "type": "session_meta",
                        "payload": {"id": "s1", "cwd": "repo"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:01.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "I found the issue in install_service.py."}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    events = load_codex_transcript_events(transcript, session_id="s1")

    assert [event.event_type for event in events] == ["session_start", "agent_message"]
    assert events[1].content == "I found the issue in install_service.py."


def test_timeline_orders_and_dedupes_by_call_id(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:03.000Z",
                        "type": "response_item",
                        "payload": {"type": "function_call_output", "call_id": "call1", "output": "ok"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:01.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call1",
                            "name": "shell_command",
                            "arguments": "{\"command\":\"echo ok\"}",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:02.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call1",
                            "name": "shell_command",
                            "arguments": "{\"command\":\"echo ok duplicate\"}",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    timeline = build_timeline(session_id="s1", transcript_paths=[transcript])

    assert [event.event_type for event in timeline.events] == ["tool_use", "tool_result"]
    assert len(timeline.edges) == 1
    assert timeline.edges[0].kind == "FOLLOWED_BY"


def test_real_session_timeline_contains_messages_tools_and_stop_events() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    transcript_path = (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "09"
        / "rollout-2026-05-09T00-33-35-019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl"
    )
    if not evidence_path.exists() or not transcript_path.exists():
        pytest.skip("selected real AMO evidence/transcript is not available in this environment")

    timeline = build_timeline(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_paths=[evidence_path],
        transcript_paths=[transcript_path],
    )

    event_types = timeline.event_types()
    assert "user_message" in event_types
    assert "agent_message" in event_types
    assert "tool_use" in event_types
    assert "tool_result" in event_types
    assert "post_tool_use" in event_types
    assert "stop" in event_types
    assert len(timeline.edges) == max(0, len(timeline.events) - 1)
    assert any("install_service.py" in "\n".join(event.files) for event in timeline.events)


def test_chunking_creates_boundary_on_file_switch() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "tool_result", "updated installer", files=("src/install_service.py",)),
            _event("e2", "tool_result", "updated hook", files=("src/hook.py",)),
        ),
        edges=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 2
    assert result.chunks[0].diagnostics == ("file_switch",)


def test_chunking_creates_boundary_on_explicit_transition_phrase() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "agent_message", "I fixed the installer path."),
            _event("e2", "agent_message", "Now let me check the dashboard CSS."),
        ),
        edges=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 2
    assert "explicit_transition" in result.chunks[0].diagnostics


def test_semantic_drift_threshold_creates_boundary_below_065() -> None:
    drift, score, reason = semantic_drift_boundary(
        ["installer hook", "install service", "capture hook"],
        "dashboard css layout",
        embedder=_KeywordEmbedder(),
        config=ChunkingConfig(semantic_drift_threshold=0.65),
    )

    assert drift is True
    assert score == 0.0
    assert reason == "semantic_drift"


def test_revisit_threshold_merges_same_file_topic() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "agent_message", "installer capture hook", files=("src/install_service.py",)),
            _event("e2", "agent_message", "dashboard css", files=("src/web/amo.css",)),
            _event("e3", "agent_message", "back to install service hook", files=("src/install_service.py",)),
        ),
        edges=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 3
    assert len(result.threads) == 2
    assert result.threads[0].metadata["continued_chunk_count"] == 1


def test_chunking_keeps_focused_same_package_writes_together() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "user_message", "make a new repo under dora/"),
            _event(
                "e2",
                "tool_use",
                "ToolFact: filesystem_write | created agent-memory-orchestrator/src/agent_memory_orchestrator",
                files=("c:/Users/sumit/Downloads/Dora/agent-memory-orchestrator/src/agent_memory_orchestrator/",),
            ),
            _event(
                "e3",
                "tool_use",
                "ToolFact: write_patch | Add File: config.py | class Settings",
                files=("agent-memory-orchestrator/src/agent_memory_orchestrator/config.py",),
            ),
            _event(
                "e4",
                "tool_use",
                "ToolFact: write_patch | Add File: db.py | CREATE TABLE sessions",
                files=("c:/Users/sumit/Downloads/Dora/agent-memory-orchestrator/src/agent_memory_orchestrator/db.py",),
            ),
        ),
        edges=(),
    )
    events = tuple(
        event if event.event_type == "user_message" else TimelineEvent(**{**event.as_dict(), "metadata": {"tool_fact": True}})
        for event in timeline.events
    )
    timeline = TimelineGraph(session_id="s1", events=events, edges=())
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 1
    assert len(result.threads) == 1
    assert "config.py" in result.chunks[0].text
    assert "db.py" in result.chunks[0].text


def test_missing_embeddings_record_fallback_without_semantic_boundary() -> None:
    drift, score, reason = semantic_drift_boundary(
        ["installer hook", "install service", "capture hook"],
        "dashboard css layout",
        embedder=None,
    )

    assert drift is False
    assert score is None
    assert reason == "embedding_status=missing"


def test_real_session_builds_decision_threads_from_selected_evidence() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    transcript_path = (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "09"
        / "rollout-2026-05-09T00-33-35-019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl"
    )
    if not evidence_path.exists() or not transcript_path.exists():
        pytest.skip("selected real AMO evidence/transcript is not available in this environment")
    timeline = build_timeline(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_paths=[evidence_path],
        transcript_paths=[transcript_path],
    )
    run = ExtractionRun.create(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_ids=("raw_639e2963e72e4e3bb063042eeb221afd",),
        transcript_paths=(str(transcript_path),),
    )

    result = build_decision_threads(timeline, extraction_run=run, embedder=HashEmbeddingProvider(dims=64))

    assert result.threads
    assert any("install_service.py" in "\n".join(thread.file_paths) for thread in result.threads)
    assert all(thread.extraction_run_id == run.id for thread in result.threads)


def test_unified_zero_hunk_parser_extracts_atomic_hunks() -> None:
    diff = """diff --git a/src/example.py b/src/example.py
--- a/src/example.py
+++ b/src/example.py
@@ -2 +2 @@
-old = 1
+new = 1
@@ -10,0 +11,2 @@
+added = True
+print(added)
"""

    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("raw1",),
    )

    assert len(hunks) == 2
    assert hunks[0].file_path == "src/example.py"
    assert hunks[0].old_start == 2
    assert hunks[0].new_start == 2
    assert hunks[1].old_count == 0
    assert hunks[1].new_count == 2


def test_added_file_hunk_with_old_start_zero_is_valid() -> None:
    diff = """diff --git a/src/new_file.py b/src/new_file.py
--- /dev/null
+++ b/src/new_file.py
@@ -0,0 +1,2 @@
+def created():
+    return True
"""
    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("commit:abc123",),
    )

    report = validate_graph_object(hunks[0])

    assert report.ok is True


def test_ast_parent_stop_rule_is_three_times_hunk_size() -> None:
    assert should_accept_ast_parent(2, 6) is True
    assert should_accept_ast_parent(2, 7) is False


def test_missing_tree_sitter_expander_creates_unparsed_code_node() -> None:
    diff = """diff --git a/src/example.py b/src/example.py
--- a/src/example.py
+++ b/src/example.py
@@ -2 +2 @@
-old = 1
+new = 1
"""
    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("raw1",),
    )

    nodes = code_nodes_from_hunks(hunks, file_contents={"src/example.py": "a = 0\nnew = 1\n"})

    assert len(nodes) == 1
    assert nodes[0].file_path == "src/example.py"
    assert nodes[0].ast_status == "unparsed"
    assert nodes[0].content == "new = 1"


def test_python_ast_expander_creates_structural_code_node_with_previous_content() -> None:
    diff = """diff --git a/src/example.py b/src/example.py
--- a/src/example.py
+++ b/src/example.py
@@ -3 +3 @@
-    return \"old\"
+    return \"new\"
"""
    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("raw1",),
    )
    before = "def launcher():\n    setup()\n    return \"old\"\n"
    after = "def launcher():\n    setup()\n    return \"new\"\n"

    nodes = code_nodes_from_hunks(
        hunks,
        file_contents={"src/example.py": after},
        old_file_contents={"src/example.py": before},
        ast_expander=python_ast_expander,
    )

    assert len(nodes) == 1
    assert nodes[0].ast_status == "parsed"
    assert nodes[0].ast_type == "Return"
    assert nodes[0].content == 'return "new"'
    assert nodes[0].prev_content == 'return "old"'
    assert nodes[0].metadata["language"] == "python"
    assert nodes[0].metadata["structural_id"]


def test_default_expander_extracts_javascript_css_and_html_regions() -> None:
    diff = """diff --git a/src/app.js b/src/app.js
--- a/src/app.js
+++ b/src/app.js
@@ -2 +2 @@
-  return oldState;
+  return nextState;
diff --git a/src/app.css b/src/app.css
--- a/src/app.css
+++ b/src/app.css
@@ -2 +2 @@
-  color: red;
+  color: green;
diff --git a/src/index.html b/src/index.html
--- a/src/index.html
+++ b/src/index.html
@@ -1 +1 @@
-<section id="graph">Old</section>
+<section id="graph">New</section>
"""
    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("raw1",),
    )
    nodes = code_nodes_from_hunks(
        hunks,
        file_contents={
            "src/app.js": "function renderGraph() {\n  return nextState;\n}\n",
            "src/app.css": ".graph-stage {\n  color: green;\n}\n",
            "src/index.html": '<section id="graph">New</section>\n',
        },
        ast_expander=default_ast_expander,
    )

    by_path = {node.file_path: node for node in nodes}
    assert by_path["src/app.js"].ast_status == "parsed"
    assert by_path["src/app.js"].metadata["language"] == "javascript"
    assert by_path["src/app.js"].metadata["symbol_kind"] == "function"
    assert by_path["src/app.js"].metadata["symbol_name"] == "renderGraph"
    assert by_path["src/app.css"].metadata["symbol_kind"] == "style_rule"
    assert by_path["src/app.css"].metadata["symbol_name"] == ".graph-stage"
    assert by_path["src/index.html"].metadata["symbol_kind"] == "markup_element"
    assert by_path["src/index.html"].metadata["symbol_name"] == "section#graph"


def test_default_expander_does_not_promote_js_control_blocks_as_functions() -> None:
    source = "\n".join(
        [
            "function renderGraph() {",
            "  if (enabled) {",
            "    draw();",
            "  }",
            "  for (const node of nodes) {",
            "    paint(node);",
            "  }",
            "}",
        ]
    )
    diff = """diff --git a/src/app.js b/src/app.js
--- a/src/app.js
+++ b/src/app.js
@@ -2 +2 @@
-  if (oldEnabled) {
+  if (enabled) {
@@ -5 +5 @@
-  for (const oldNode of nodes) {
+  for (const node of nodes) {
"""
    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("raw1",),
    )
    nodes = code_nodes_from_hunks(
        hunks,
        file_contents={"src/app.js": source},
        ast_expander=default_ast_expander,
    )

    assert {node.metadata["symbol_name"] for node in nodes} == {"renderGraph"}
    assert all(node.metadata["symbol_kind"] == "function" for node in nodes)


def test_large_added_python_file_compacts_to_top_level_regions() -> None:
    source_lines = [
        "import json",
        "import os",
        "",
        "CONSTANT = 1",
        "",
        "class Worker:",
        "    def run(self):",
        "        return True",
        "",
    ]
    for index in range(1, 31):
        source_lines.extend(
            [
                f"def helper_{index}():",
                f"    value = {index}",
                "    if value:",
                "        return value",
                "    return 0",
                "",
            ]
        )
    source = "\n".join(source_lines)
    diff = "\n".join(
        [
            "diff --git a/src/large.py b/src/large.py",
            "--- /dev/null",
            "+++ b/src/large.py",
            f"@@ -0,0 +1,{len(source_lines)} @@",
            *[f"+{line}" for line in source_lines],
        ]
    )
    hunks = parse_unified_zero_hunks(
        diff,
        session_id="s1",
        extraction_run_id="run1",
        commit_id="abc123",
        evidence_ids=("raw1",),
    )

    nodes = code_nodes_from_hunks(
        hunks,
        file_contents={"src/large.py": source},
        ast_expander=python_ast_expander,
    )

    assert len(nodes) <= 24
    assert {node.ast_type for node in nodes} >= {"ImportBlock", "ModuleAssignmentBlock", "ClassDef"}
    assert any(node.ast_type == "FunctionDef" for node in nodes)
    assert all("compaction_reason" in node.metadata for node in nodes)


def test_real_commit_produces_code_hunks_and_nodes_without_whole_file_blobs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "c5326f8^{commit}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if verify.returncode != 0:
        pytest.skip("selected real commit c5326f8 is not available")

    hunks, nodes = extract_code_nodes_from_commit(
        repo_root=repo_root,
        commit="c5326f8",
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        extraction_run_id="extraction_run:019e08eb-8f1f-7381-8f25-59344c4ac8a9:raw_639e",
        evidence_ids=("raw_639e2963e72e4e3bb063042eeb221afd",),
        file_path="src/agent_memory_orchestrator/install_service.py",
    )

    assert hunks
    assert nodes
    assert {hunk.file_path for hunk in hunks} == {"src/agent_memory_orchestrator/install_service.py"}
    assert all(node.commit_id == "c5326f8" for node in nodes)
    assert all(node.evidence_ids for node in nodes)
    hunk_by_id = {hunk.id: hunk for hunk in hunks}
    for node in nodes:
        hunk = hunk_by_id[str(node.metadata["hunk_id"])]
        assert len(node.content.splitlines()) <= max(1, hunk.new_count) * 3
        assert len(node.content.splitlines()) < 150
        assert node.metadata["language"] == "python"
    assert any(node.ast_status == "parsed" for node in nodes)


def test_commit_windows_create_deterministic_work_changes() -> None:
    changes = work_changes_from_commit_windows(
        [
            {
                "window_id": "window:commit:abc123",
                "commit_id": "abc123",
                "full_sha": "abc123456789",
                "message": "feat(memory): add local memory engine",
                "ordinal": 1,
                "start_index": 10,
                "end_index": 20,
                "event_count": 11,
                "message_event_count": 4,
                "tool_event_count": 7,
                "git_changed_files": ["src/memory.py", "tests/test_memory.py"],
                "git_name_status": [{"status": "A", "path": "src/memory.py"}],
            },
            {"commit_id": "abcdef1", "message": "fake smoke commit"},
        ],
        session_id="s1",
        extraction_run_id="run1",
    )

    assert len(changes) == 1
    assert changes[0].kind == "WorkChange"
    assert changes[0].evidence_ids == ("commit:abc123",)
    assert changes[0].metadata["commit_category"] == "feat"
    assert "src/memory.py" in changes[0].summary


def test_session_graph_query_ranks_specific_work_change() -> None:
    store = _SessionQueryStore(
        [
            {
                "id": "work:s1:05f0f49",
                "kind": "WorkChange",
                "label": "feat(installer): add npx wrapper package",
                "summary": "Add npm installer wrapper.",
                "session_id": "s1",
                "metadata": {"commit_message": "feat(installer): add npx wrapper package"},
            },
            {
                "id": "work:s1:25c77b6",
                "kind": "WorkChange",
                "label": "feat(reasoning-graph): add focused evidence windows",
                "summary": "Build cleaned focused evidence windows and tool facts for reasoning graph chunking.",
                "session_id": "s1",
                "metadata": {"commit_message": "feat(reasoning-graph): add focused evidence windows"},
            },
        ],
        neighbors={"work:s1:25c77b6": [{"id": "commit:25c77b6", "kind": "GitCommit", "commit_id": "25c77b6"}]},
    )

    hits = query_session_graph(store, "focused evidence windows", session_id="s1", kinds=["WorkChange"])

    assert hits
    assert hits[0].node["id"] == "work:s1:25c77b6"
    assert hits[0].neighbors[0]["commit_id"] == "25c77b6"


def test_same_ast_same_topic_creates_code_version_edge() -> None:
    old = _code_node("code:old", "return old_launcher")
    new = _code_node("code:new", "return launcher_hardened")
    old_thread = _thread("old", "install service hook", files=(old.file_path,))
    new_thread = _thread("new", "install service hook", files=(new.file_path,))

    plan = resolve_code_node_version(
        new_node=new,
        new_thread=new_thread,
        candidates=[old],
        candidate_threads={old.id: old_thread},
        embedder=_KeywordEmbedder(),
    )

    assert len(plan.relations) == 1
    assert plan.relations[0].kind == "SUPERSEDED_BY"
    assert plan.relations[0].source_id == old.id
    assert plan.relations[0].target_id == new.id
    assert plan.new_node.prev_content == old.content


def test_same_file_different_topic_does_not_create_code_version_edge() -> None:
    old = _code_node("code:old", "return launcher")
    new = _code_node("code:new", "return launcher_hardened")
    old_thread = _thread("old", "dashboard css", files=(old.file_path,))
    new_thread = _thread("new", "install service hook", files=(new.file_path,))

    plan = resolve_code_node_version(
        new_node=new,
        new_thread=new_thread,
        candidates=[old],
        candidate_threads={old.id: old_thread},
        embedder=_KeywordEmbedder(),
    )

    assert plan.relations == ()
    assert any(item.startswith("unrelated_same_file") for item in plan.diagnostics)


def test_revert_signal_creates_reverts_relation_and_prev_content() -> None:
    old = _code_node("code:old", "return launcher")
    new = _code_node("code:new", "return original_launcher")
    old_thread = _thread("old", "install service hook", files=(old.file_path,))
    new_thread = _thread("new", "revert install service hook", files=(new.file_path,))

    plan = resolve_code_node_version(
        new_node=new,
        new_thread=new_thread,
        candidates=[old],
        candidate_threads={old.id: old_thread},
        embedder=_KeywordEmbedder(),
    )

    assert len(plan.relations) == 1
    assert plan.relations[0].kind == "REVERTS"
    assert plan.relations[0].source_id == new.id
    assert plan.relations[0].target_id == old.id
    assert plan.relations[0].old_status == "superseded"
    assert plan.new_node.prev_content == old.content


def test_real_repeated_same_file_evidence_can_drive_version_resolution() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    if not evidence_path.exists():
        pytest.skip("selected real AMO evidence is not available in this environment")
    wanted = {
        "raw_3ce293ed37ce4d7ebabae7c1116bdd69",
        "raw_850a43197504432bafb15e01f384af28",
    }
    payloads: dict[str, str] = {}
    with evidence_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("id") not in wanted:
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
            command = str(tool_input.get("command") or "")
            payloads[str(item["id"])] = command
    if set(payloads) != wanted:
        pytest.skip("selected repeated same-file evidence ids are not all available")

    assert all("install_service.py" in command for command in payloads.values())
    old = _code_node("code:real-old", payloads["raw_3ce293ed37ce4d7ebabae7c1116bdd69"][:600])
    new = _code_node("code:real-new", payloads["raw_850a43197504432bafb15e01f384af28"][:600])
    old_thread = _thread("real-old", "install service hook launcher", files=(old.file_path,))
    new_thread = _thread("real-new", "install service hook launcher", files=(new.file_path,))

    plan = resolve_code_node_version(
        new_node=new,
        new_thread=new_thread,
        candidates=[old],
        candidate_threads={old.id: old_thread},
        embedder=_KeywordEmbedder(),
    )

    assert plan.relations
    assert plan.new_node.prev_content == old.content


def test_deterministic_decision_patterns_create_expected_nodes() -> None:
    thread = _thread("decision-thread", "install service hook", files=("src/install_service.py",), event_ids=("e1",))
    event = _event("e1", "agent_message", "I'll harden the hook launcher because SystemExit should not break capture.")
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = extract_decisions(thread=thread, events=[event], extraction_run=run)

    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.kind == "Decision"
    assert decision.confidence == 0.60
    assert decision.evidence_ids == ("raw1",)
    assert decision.metadata["decision_type"] == "planned_action"


def test_qwen_invalid_schema_becomes_diagnostic_only() -> None:
    thread = _thread("qwen-thread", "install service hook", files=("src/install_service.py",))
    event = _event("e1", "agent_message", "The hook launcher needs cleanup.")
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = extract_decisions(thread=thread, events=[event], extraction_run=run, qwen=_QwenDecisionStub({"bad": []}))

    assert result.decisions == ()
    assert result.review_candidates == ()
    assert result.diagnostics[0]["error_type"] == "schema_mismatch"


def test_qwen_low_confidence_becomes_review_candidate() -> None:
    thread = _thread("qwen-thread", "install service hook", files=("src/install_service.py",))
    event = _event("e1", "tool_result", "patched install_service.py", files=("src/install_service.py",))
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))
    qwen = _QwenDecisionStub(
        {
            "decisions": [
                {
                    "decision_type": "completed_fix",
                    "subject": "hook launcher",
                    "predicate": "hardened",
                    "object": "SystemExit handling",
                    "reason": "capture should continue",
                    "confidence": 0.2,
                    "evidence_event_ids": ["raw1"],
                }
            ]
        }
    )

    result = extract_decisions(thread=thread, events=[event], extraction_run=run, qwen=qwen)

    assert result.decisions == ()
    assert result.review_candidates[0]["reason"] == "low_confidence"


def test_qwen_valid_output_creates_extraction_run_scoped_decision() -> None:
    thread = _thread("qwen-thread", "install service hook", files=("src/install_service.py",))
    event = _event("e1", "tool_result", "patched install_service.py", files=("src/install_service.py",))
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))
    qwen = _QwenDecisionStub(
        {
            "decisions": [
                {
                    "decision_type": "completed_fix",
                    "subject": "hook launcher",
                    "predicate": "handles",
                    "object": "SystemExit",
                    "reason": "capture should fail open",
                    "confidence": 0.82,
                    "evidence_event_ids": ["raw1"],
                }
            ]
        }
    )

    result = extract_decisions(thread=thread, events=[event], extraction_run=run, qwen=qwen)

    assert len(result.decisions) == 1
    assert result.decisions[0].kind == "Fix"
    assert result.decisions[0].source == "qwen"
    assert result.decisions[0].qwen_call == "decision_extraction_fallback"
    assert result.decisions[0].extraction_run_id == run.id


def test_real_session_extracts_evidence_backed_decisions_from_threads() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    transcript_path = (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "09"
        / "rollout-2026-05-09T00-33-35-019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl"
    )
    if not evidence_path.exists() or not transcript_path.exists():
        pytest.skip("selected real AMO evidence/transcript is not available in this environment")
    timeline = build_timeline(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_paths=[evidence_path],
        transcript_paths=[transcript_path],
    )
    run = ExtractionRun.create(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_ids=("raw_639e2963e72e4e3bb063042eeb221afd",),
        transcript_paths=(str(transcript_path),),
    )
    built = build_decision_threads(timeline, extraction_run=run, embedder=HashEmbeddingProvider(dims=64))

    extracted = [
        decision
        for thread in built.threads
        for decision in extract_decisions(thread=thread, events=list(timeline.events), extraction_run=run).decisions
    ]

    assert extracted
    assert all(decision.evidence_ids for decision in extracted)
    assert all(decision.extraction_run_id == run.id for decision in extracted)


def test_produced_change_edges_link_decision_to_code_by_thread_file() -> None:
    decision = DecisionUnit(
        id="decision:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        summary="Fixed installer hook",
        evidence_ids=("raw-decision",),
        kind="Fix",
    )
    code = _code_node("code:s1:1", "def hook(): pass")
    thread = _thread("thread1", "install service hook", files=(code.file_path,))

    edges = produced_change_edges(decisions=[decision], code_nodes=[code], thread=thread)

    assert len(edges) == 1
    assert edges[0].kind == "PRODUCED_CHANGE_IN"
    assert edges[0].source_id == decision.id
    assert edges[0].target_id == code.id
    assert edges[0].evidence_ids == ("raw-decision", "raw1")


def test_produced_change_edges_match_repo_prefixed_thread_paths() -> None:
    decision = DecisionUnit(
        id="decision:s1:repo-prefixed",
        session_id="s1",
        extraction_run_id="run1",
        summary="Fixed installer hook",
        evidence_ids=("raw-decision",),
        kind="Fix",
    )
    code = _code_node("code:s1:repo-relative", "def hook(): pass")
    thread = _thread(
        "thread-repo-prefixed",
        "install service hook",
        files=("agent-memory-orchestrator/src/agent_memory_orchestrator/install_service.py",),
    )

    edges = produced_change_edges(decisions=[decision], code_nodes=[code], thread=thread)

    assert len(edges) == 1
    assert edges[0].kind == "PRODUCED_CHANGE_IN"
    assert edges[0].target_id == code.id


def test_produced_change_edges_prefers_symbol_and_ast_matches() -> None:
    decision = DecisionUnit(
        id="decision:s1:ranked",
        session_id="s1",
        extraction_run_id="run1",
        summary="Fixed hook launcher execution",
        evidence_ids=("raw-decision",),
        kind="Fix",
    )
    good = _code_node("code:s1:good", "def hook_launcher():\n    return True", line_start=10, line_end=11)
    good = CodeNode(
        **{
            **good.as_dict(),
            "ast_status": "parsed",
            "metadata": {"symbol_name": "hook_launcher", "structural_id": "src/install_service.py::FunctionDef:hook_launcher"},
        }
    )
    weak = _code_node("code:s1:weak", "settings = read_config()", line_start=90, line_end=90)
    thread = _thread("thread-ranked", "install service hook", files=(good.file_path,))

    edges = produced_change_edges(
        decisions=[decision],
        code_nodes=[weak, good],
        thread=thread,
        max_edges_per_decision=1,
    )

    assert len(edges) == 1
    assert edges[0].target_id == good.id
    assert "symbol_match" in edges[0].metadata["link_reasons"]


def test_code_node_provenance_edges_keep_code_search_explainable() -> None:
    code = _code_node("code:s1:provenance", "def hook(): pass")

    edges = code_node_provenance_edges(extraction_run_id="run1", code_nodes=[code])

    assert len(edges) == 1
    assert edges[0].source_id == "run1"
    assert edges[0].target_id == code.id
    assert edges[0].kind == "CREATED_CODE_NODE"
    assert edges[0].metadata["file_path"] == code.file_path


def test_work_change_links_to_code_nodes_and_commit() -> None:
    change = DecisionUnit(
        id="work:s1:c5326f8",
        session_id="s1",
        extraction_run_id="run1",
        summary="fix(hooks): harden installer hook.",
        evidence_ids=("commit:c5326f8",),
        kind="WorkChange",
        confidence=1.0,
        metadata={
            "commit_id": "c5326f8",
            "full_sha": "c5326f8abc",
            "commit_message": "fix(hooks): harden installer hook",
            "git_changed_files": ["src/agent_memory_orchestrator/install_service.py"],
        },
    )
    code = _code_node("code:s1:c5326f8:hook", "def hook(): pass")

    code_edges = work_change_code_edges(work_changes=[change], code_nodes=[code])
    commit_edges = work_change_commit_edges(work_changes=[change])
    code_commit_edges = code_node_commit_edges(code_nodes=[code])

    assert len(code_edges) == 1
    assert code_edges[0].kind == "PRODUCED_CHANGE_IN"
    assert code_edges[0].source_id == change.id
    assert code_edges[0].target_id == code.id
    assert code_edges[0].metadata["commit_id"] == "c5326f8"
    assert validate_reasoning_edge(code_edges[0]).ok is True
    assert commit_edges[0].kind == "COMMITTED_AS"
    assert commit_edges[0].target_id == "commit:c5326f8"
    assert validate_reasoning_edge(commit_edges[0]).ok is True
    assert code_commit_edges[0].kind == "LINKED_TO_COMMIT"
    assert code_commit_edges[0].source_id == code.id
    assert validate_reasoning_edge(code_commit_edges[0]).ok is True


def test_passing_test_after_write_creates_validated_by_and_bumps_once() -> None:
    decision = DecisionUnit(
        id="decision:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        summary="Fixed installer hook",
        evidence_ids=("raw-decision",),
        kind="Fix",
        confidence=0.8,
    )
    test = GraphTestRun(
        id="test:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        command="pytest tests/test_hook_cli.py",
        result="pass",
        evidence_ids=("raw-test",),
        metadata={"event_id": "test-event"},
    )

    result = validation_edges_for_test(
        decision=decision,
        test_run=test,
        event_order={"write-event": 10, "test-event": 20},
        write_event_ids=("write-event",),
    )

    assert len(result.edges) == 1
    assert result.edges[0].kind == "VALIDATED_BY"
    assert result.decision.confidence == 0.9


def test_passing_test_before_write_does_not_validate() -> None:
    decision = DecisionUnit(
        id="decision:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        summary="Fixed installer hook",
        evidence_ids=("raw-decision",),
        kind="Fix",
        confidence=0.8,
    )
    test = GraphTestRun(
        id="test:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        command="pytest",
        result="pass",
        evidence_ids=("raw-test",),
        metadata={"event_id": "test-event"},
    )

    result = validation_edges_for_test(
        decision=decision,
        test_run=test,
        event_order={"test-event": 5, "write-event": 10},
        write_event_ids=("write-event",),
    )

    assert result.edges == ()
    assert result.diagnostics == ("test_before_write",)
    assert result.decision.confidence == 0.8


def test_failed_test_after_write_creates_failed_validation_without_bump() -> None:
    decision = DecisionUnit(
        id="decision:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        summary="Fixed installer hook",
        evidence_ids=("raw-decision",),
        kind="Fix",
        confidence=0.8,
    )
    test = GraphTestRun(
        id="test:s1:1",
        session_id="s1",
        extraction_run_id="run1",
        command="pytest",
        result="fail",
        evidence_ids=("raw-test",),
        metadata={"event_id": "test-event"},
    )

    result = validation_edges_for_test(
        decision=decision,
        test_run=test,
        event_order={"write-event": 10, "test-event": 20},
        write_event_ids=("write-event",),
    )

    assert len(result.edges) == 1
    assert result.edges[0].kind == "FAILED_VALIDATION"
    assert result.decision.confidence == 0.8


def test_real_session_decision_links_to_real_commit_code_node() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "c5326f8^{commit}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if verify.returncode != 0:
        pytest.skip("selected real commit c5326f8 is not available")
    hunks, code_nodes = extract_code_nodes_from_commit(
        repo_root=repo_root,
        commit="c5326f8",
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        extraction_run_id="run-real",
        evidence_ids=("raw_639e2963e72e4e3bb063042eeb221afd",),
        file_path="src/agent_memory_orchestrator/install_service.py",
    )
    if not hunks or not code_nodes:
        pytest.skip("selected real commit did not produce install_service.py code nodes")
    decision = DecisionUnit(
        id="decision:real:1",
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        extraction_run_id="run-real",
        summary="Fixed installer hook",
        evidence_ids=("raw_639e2963e72e4e3bb063042eeb221afd",),
        kind="Fix",
    )
    thread = DecisionThread(
        id="thread:real:1",
        session_id=decision.session_id,
        extraction_run_id="run-real",
        event_ids=("event:raw_639e2963e72e4e3bb063042eeb221afd",),
        topic="install service hook",
        file_paths=("src/agent_memory_orchestrator/install_service.py",),
        evidence_ids=decision.evidence_ids,
    )

    edges = produced_change_edges(decisions=[decision], code_nodes=code_nodes, thread=thread)

    assert edges
    assert all(edge.kind == "PRODUCED_CHANGE_IN" for edge in edges)
