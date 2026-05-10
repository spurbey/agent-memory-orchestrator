from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .graph_diagnostics import debug_drain, debug_graph, debug_hooks, debug_qwen
from .graph_service import GraphRagService
from .graph_store import GraphBackendUnavailable
from .memory_service import MemoryService
from .qwen_client import QwenUnavailable

_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
_GRAPH_LOCK = threading.RLock()


def _bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class AmoHandler(BaseHTTPRequestHandler):
    settings: Settings

    def _write_html(self, status: int, body: str) -> bool:
        encoded = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def _write_json(self, status: int, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._write_html(200, SESSION_COCKPIT_HTML)
            return
        if path == "/dashboard":
            self._write_html(200, DASHBOARD_HTML)
            return
        if path == "/sessions":
            self._write_html(200, SESSION_COCKPIT_HTML)
            return
        if path == "/graph":
            self._write_html(200, GRAPH_HTML)
            return
        if path == "/graph3d":
            self._write_html(200, GRAPH3D_HTML)
            return
        if path == "/health":
            self._write_json(
                200,
                {
                    "ok": True,
                    "service": "agent-memory-orchestrator",
                    "graph_backend": self.settings.graph_backend,
                    "graph_path": str(self.settings.graph_path),
                    "qwen_runtime": self.settings.qwen_runtime,
                    "qwen_model": self.settings.qwen_model,
                    "qwen_timeout_seconds": self.settings.qwen_timeout_seconds,
                    "qwen_planner_timeout_seconds": self.settings.qwen_planner_timeout_seconds,
                    "qwen_extract_timeout_seconds": self.settings.qwen_extract_timeout_seconds,
                    "qwen_compress_timeout_seconds": self.settings.qwen_compress_timeout_seconds,
                    "qwen_num_ctx": self.settings.qwen_num_ctx,
                    "drain_max_windows_per_run": self.settings.drain_max_windows_per_run,
                },
            )
            return
        if path == "/metrics":
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                self._write_json(200, svc.inspect_metrics())
            finally:
                svc.close()
            return
        if path.startswith("/api/graph/") or path.startswith("/api/debug/") or path == "/api/graph-merge-status":
            try:
                raw_limit = (query.get("limit") or ["25"])[0]
                limit = _bounded_int(raw_limit, default=25, minimum=1, maximum=500)
                session_id = (query.get("session_id") or [""])[0]
                if path == "/api/debug/hooks":
                    self._write_json(200, debug_hooks(self.settings))
                    return
                if path == "/api/debug/qwen":
                    sample = (query.get("sample") or ["Classify a decision lookup query."])[0]
                    self._write_json(200, debug_qwen(self.settings, sample=sample))
                    return
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        if path == "/api/graph/status" or path == "/api/graph-merge-status":
                            self._write_json(200, graph.merge_status(session_id=session_id))
                            return
                        if path == "/api/graph/session-context":
                            self._write_json(200, graph.current_context(session_id=session_id, limit=limit))
                            return
                        if path == "/api/graph/raw-evidence":
                            graph_query = (query.get("query") or query.get("q") or [""])[0]
                            self._write_json(200, graph.raw_evidence(query=graph_query, limit=limit))
                            return
                        if path == "/api/graph/work-trace":
                            commit = (query.get("commit") or ["HEAD"])[0] or "HEAD"
                            cwd = (query.get("cwd") or [""])[0] or None
                            self._write_json(200, graph.work_trace(commit=commit, cwd=cwd))
                            return
                        if path == "/api/graph/sessions":
                            self._write_json(200, graph.session_overview(limit=limit))
                            return
                        if path == "/api/graph/session-detail":
                            self._write_json(200, graph.session_detail(session_id=session_id, limit=limit))
                            return
                        if path == "/api/graph/central":
                            self._write_json(200, graph.central_graph(limit=limit))
                            return
                        if path == "/api/debug/drain":
                            self._write_json(200, debug_drain(graph._new_drain(), session_id=session_id))  # noqa: SLF001
                            return
                        if path == "/api/debug/graph":
                            self._write_json(200, debug_graph(graph, session_id=session_id))
                            return
                        if path == "/api/debug/cleanup-noisy":
                            apply = (query.get("apply") or ["false"])[0].lower() == "true"
                            self._write_json(200, graph.cleanup_noisy_drafts(limit=limit, apply=apply))
                            return
                        if path == "/api/debug/consolidate":
                            apply = (query.get("apply") or ["false"])[0].lower() == "true"
                            self._write_json(200, graph.consolidate_graph(limit=limit, apply=apply))
                            return
                        if path == "/api/debug/graph-cache":
                            self._write_json(200, graph.graph_cache_status())
                            return
                    finally:
                        graph.close()
            except _CLIENT_ABORT_ERRORS:
                return
            except (GraphBackendUnavailable, QwenUnavailable) as exc:
                self._write_json(200, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                self._write_json(500, {"ok": False, "error": str(exc)})
                return
        if path.startswith("/api/"):
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                raw_limit = (query.get("limit") or ["25"])[0]
                limit = _bounded_int(raw_limit, default=25, minimum=1, maximum=100)
                session_id = (query.get("session_id") or [""])[0] or None
                if path == "/api/dashboard":
                    self._write_json(200, {"ok": True, "data": svc.dashboard_snapshot(limit=limit)})
                    return
                if path == "/api/graph":
                    include_historical = (query.get("include_historical") or ["false"])[0].lower() == "true"
                    graph_query = (query.get("query") or query.get("q") or [""])[0] or None
                    min_confidence_raw = (query.get("min_confidence") or [""])[0]
                    min_confidence = float(min_confidence_raw) if min_confidence_raw else None
                    graph_limit = _bounded_int(raw_limit, default=100, minimum=10, maximum=500)
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "graph": svc.graph_snapshot(
                                query=graph_query,
                                session_id=session_id,
                                limit=graph_limit,
                                include_historical=include_historical,
                                relation=(query.get("relation") or [""])[0] or None,
                                node_type=(query.get("node_type") or [""])[0] or None,
                                memory_type=(query.get("memory_type") or [""])[0] or None,
                                min_confidence=min_confidence,
                            ),
                        },
                    )
                    return
                if path == "/api/sessions":
                    self._write_json(200, {"ok": True, "sessions": svc.list_sessions(limit=limit)})
                    return
                if path == "/api/events":
                    self._write_json(200, {"ok": True, "events": svc.list_events(session_id=session_id, limit=limit)})
                    return
                if path == "/api/memories":
                    include_historical = (query.get("include_historical") or ["true"])[0].lower() != "false"
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "memories": svc.list_memory_units(
                                session_id=session_id,
                                limit=limit,
                                include_historical=include_historical,
                            ),
                        },
                    )
                    return
                if path == "/api/retrieval-runs":
                    self._write_json(200, {"ok": True, "retrieval_runs": svc.list_retrieval_runs(limit=limit)})
                    return
                if path.startswith("/api/retrieval-runs/"):
                    run_id = path.rsplit("/", 1)[-1]
                    self._write_json(200, {"ok": True, "detail": svc.retrieval_run_detail(run_id)})
                    return
            except _CLIENT_ABORT_ERRORS:
                return
            except Exception as exc:
                self._write_json(500, {"ok": False, "error": str(exc)})
                return
            finally:
                svc.close()
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid json: {exc}"})
            return

        try:
            if self.path == "/hooks/ingest":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        result = graph.capture_hook(payload, default_agent=str(payload.get("agent") or "codex"))
                        self._write_json(200, {"ok": True, **result})
                    finally:
                        graph.close()
                return
            if self.path == "/graph/search":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=8, minimum=1, maximum=50)
                        result = graph.graph_search(
                            query=str(payload.get("query") or ""),
                            limit=limit,
                            include_raw=bool(payload.get("include_raw")),
                            include_historical=bool(payload.get("include_historical")),
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/drain":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        max_windows = _bounded_int(
                            str(payload.get("max_windows") or ""),
                            default=self.settings.drain_max_windows_per_run,
                            minimum=1,
                            maximum=25,
                        )
                        result = graph.drain_evidence(
                            limit=limit,
                            session_id=str(payload.get("session_id") or ""),
                            max_windows=max_windows,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/work-trace":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        result = graph.work_trace(
                            commit=str(payload.get("commit") or "HEAD"),
                            cwd=payload.get("cwd") or None,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/cleanup-noisy":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        result = graph.cleanup_noisy_drafts(limit=limit, apply=bool(payload.get("apply")))
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/consolidate":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        result = graph.consolidate_graph(limit=limit, apply=bool(payload.get("apply")))
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/rebuild-cache":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=5000, minimum=1, maximum=20000)
                        result = graph.rebuild_graph_cache(limit=limit)
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/finalize-session":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        result = graph.finalize_session(
                            session_id=str(payload.get("session_id") or ""),
                            commit=str(payload.get("commit") or "HEAD"),
                            apply=bool(payload.get("apply")),
                            limit=limit,
                            cwd=payload.get("cwd") or None,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/rebuild-central":
                with _GRAPH_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=100000, minimum=1, maximum=500000)
                        max_windows = payload.get("max_windows")
                        bounded_windows = (
                            _bounded_int(str(max_windows), default=self.settings.drain_max_windows_per_run, minimum=1, maximum=1000)
                            if max_windows
                            else None
                        )
                        result = graph.rebuild_central_from_evidence(
                            apply=bool(payload.get("apply")),
                            backup_current=bool(payload.get("backup_current")) or bool(payload.get("apply")),
                            limit=limit,
                            max_windows=bounded_windows,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                if self.path == "/memory/search":
                    limit = _bounded_int(str(payload.get("limit") or ""), default=10, minimum=1, maximum=50)
                    result = svc.search_memories(
                        query=str(payload.get("query") or ""),
                        session_id=payload.get("session_id") or None,
                        limit=limit,
                    )
                    self._write_json(200, {"ok": True, "results": result})
                    return
            finally:
                svc.close()
            self._write_json(404, {"error": "not found"})
        except _CLIENT_ABORT_ERRORS:
            return
        except (GraphBackendUnavailable, QwenUnavailable) as exc:
            self._write_json(200, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


SESSION_COCKPIT_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AMO Session Cockpit</title>
  <style>
    :root {
      --bg: #0d1110;
      --panel: #14201b;
      --panel-2: #1b2c24;
      --ink: #f1f8ef;
      --muted: #9ab1a4;
      --line: #2f463a;
      --accent: #b9f66b;
      --blue: #8ecae6;
      --warn: #ffd166;
      --bad: #ff6b6b;
      --violet: #c8b6ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 10% 0%, rgba(185, 246, 107, 0.16), transparent 34rem),
        radial-gradient(circle at 86% 8%, rgba(142, 202, 230, 0.11), transparent 30rem),
        linear-gradient(140deg, #0d1110 0%, #111c18 58%, #0a0e0d 100%);
      font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }
    header {
      padding: 22px 28px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
    }
    h1 { margin: 0; font-size: 23px; letter-spacing: -0.04em; }
    .subtitle { color: var(--muted); margin-top: 6px; max-width: 900px; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    button, input, select {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--ink);
      border-radius: 11px;
      padding: 9px 11px;
      font: inherit;
    }
    button { cursor: pointer; color: var(--accent); }
    button.secondary { color: var(--blue); }
    input { min-width: 360px; }
    main { padding: 20px 28px 42px; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 15px; align-items: start; }
    .card {
      background: rgba(20, 32, 27, 0.94);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 15px;
      min-width: 0;
      box-shadow: 0 22px 70px rgba(0,0,0,0.24);
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-7 { grid-column: span 7; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    h2 { font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.12em; margin: 0 0 11px; }
    h3 { font-size: 14px; margin: 0 0 8px; color: var(--accent); }
    .metric { font-size: 26px; color: var(--accent); margin: 2px 0; }
    .label { color: var(--muted); font-size: 12px; }
    .item { padding: 10px 0; border-top: 1px solid rgba(47,70,58,0.72); }
    .item:first-child { border-top: 0; padding-top: 0; }
    .row { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
    .stack { display: flex; flex-direction: column; gap: 8px; }
    .pill {
      display: inline-block;
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--blue);
      font-size: 12px;
      white-space: nowrap;
    }
    .pill.good { color: var(--accent); }
    .pill.warn { color: var(--warn); }
    .pill.bad { color: var(--bad); }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .clickable { cursor: pointer; }
    .clickable:hover { color: var(--accent); }
    .selected { outline: 1px solid var(--accent); border-radius: 12px; padding-inline: 8px; margin-inline: -8px; }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #09100d;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 11px;
      max-height: 420px;
      overflow: auto;
    }
    details { border: 1px solid rgba(47,70,58,0.7); border-radius: 13px; padding: 9px 11px; background: rgba(9,16,13,0.36); }
    summary { cursor: pointer; color: var(--blue); }
    .pipeline { display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px; }
    .stage { padding: 10px; border: 1px solid var(--line); border-radius: 13px; background: rgba(27,44,36,0.75); min-height: 74px; }
    .stage .num { color: var(--accent); font-size: 18px; }
    svg { width: 100%; min-height: 360px; background: #09100d; border: 1px solid var(--line); border-radius: 14px; }
    .node-dot { stroke: #09100d; stroke-width: 2; }
    @media (max-width: 1100px) {
      .span-3, .span-4, .span-5, .span-6, .span-7, .span-8 { grid-column: span 12; }
      .pipeline { grid-template-columns: repeat(2, 1fr); }
      header { flex-direction: column; }
      input { min-width: 0; width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AMO Session Cockpit</h1>
      <div class="subtitle">Session-first view over raw capture, cleaned evidence windows, Qwen extraction, Kuzu draft graph, central graph, and explicit retrieval. The daemon owns all graph/runtime state.</div>
    </div>
    <div class="toolbar">
      <input id="query" placeholder="Ask graph memory, e.g. why did graph search return zero" />
      <button onclick="runRetrieval()">Graph Search</button>
      <button class="secondary" onclick="refreshAll()">Refresh</button>
      <a href="/graph3d"><button class="secondary">3D Final Graph</button></a>
      <a href="/graph"><button class="secondary">Legacy Graph</button></a>
      <a href="/dashboard"><button class="secondary">Legacy Dashboard</button></a>
    </div>
  </header>
  <main class="grid">
    <section class="card span-12">
      <h2>Pipeline</h2>
      <div class="pipeline" id="pipeline"></div>
    </section>
    <section class="card span-4">
      <div class="row"><h2>Captured Sessions</h2><button onclick="loadSessions()">Reload</button></div>
      <div id="sessions" class="stack"></div>
    </section>
    <section class="card span-8">
      <h2>Current Session Context</h2>
      <div id="contextBox" class="muted">Select a session.</div>
    </section>
    <section class="card span-5">
      <div class="row"><h2>Captured Timeline</h2><button onclick="drainSelected()">Drain 5</button></div>
      <div id="timeline" class="stack"></div>
    </section>
    <section class="card span-7">
      <h2>Cleaned Evidence Windows</h2>
      <div id="windows" class="stack"></div>
    </section>
    <section class="card span-12">
      <h2>Merge Preview</h2>
      <div class="subtitle">Dry-run view of answer-grade draft nodes that would promote on graph-finalize-session, plus version edges that would be created.</div>
      <div id="mergePreview" class="stack"></div>
    </section>
    <section class="card span-6">
      <h2>Session Graph</h2>
      <div id="sessionGraphStats" class="small muted"></div>
      <svg id="sessionGraph"></svg>
      <div id="sessionGraphList"></div>
    </section>
    <section class="card span-6">
      <h2>Central / Final Graph</h2>
      <div id="centralStats" class="small muted"></div>
      <svg id="centralGraph"></svg>
      <div id="centralList"></div>
    </section>
    <section class="card span-12">
      <h2>Retrieval Output</h2>
      <pre id="retrieval">Run a query to see planner, candidates, raw_included, timings, and returned context.</pre>
    </section>
  </main>
  <script>
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const short = (value, n = 170) => {
      const text = String(value ?? "").replace(/\s+/g, " ");
      return text.length > n ? text.slice(0, n - 3) + "..." : text;
    };
    const pretty = value => JSON.stringify(value ?? {}, null, 2);
    let selectedSession = "";

    async function getJson(url, options) {
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
      return data;
    }

    function renderPipeline() {
      const stages = [
        ["1", "Captured", "Hook appends raw JSONL evidence."],
        ["2", "Pending Drain", "Cursor tracks unprocessed rows."],
        ["3", "Triggered", "Write/test/git/finalize windows are selected."],
        ["4", "Cleaned", "Noisy raw artifacts become bounded Qwen input."],
        ["5", "Qwen Delta", "Goal, decision, files, tests, fixes are extracted."],
        ["6", "Draft Graph", "Kuzu session nodes/edges are written."],
        ["7", "Commit Link", "GitCommit, COMMITTED_AS, MERGED_INTO edges."],
        ["8", "Searchable", "Explicit GraphRAG returns answer-grade context."]
      ];
      document.getElementById("pipeline").innerHTML = stages.map(([num, title, body]) => `
        <div class="stage"><div class="num">${num}</div><strong>${esc(title)}</strong><div class="small muted">${esc(body)}</div></div>
      `).join("");
    }

    async function loadSessions() {
      const data = await getJson("/api/graph/sessions?limit=40");
      const sessions = data.sessions || [];
      document.getElementById("sessions").innerHTML = sessions.length ? sessions.map(row => {
        const context = row.latest_context || {};
        const counts = row.graph_counts || {};
        const isSelected = row.session_id === selectedSession;
        return `<div class="item clickable ${isSelected ? "selected" : ""}" onclick="selectSession('${esc(row.session_id)}')">
          <div class="row"><strong>${esc(short(row.session_id, 34))}</strong><span class="pill">${esc(row.latest_event)}</span></div>
          <div class="small muted">${esc((row.source_apps || []).join(", "))} | raw ${esc(row.raw_events)} | draft ${esc(counts.draft || 0)} | committed ${esc(counts.committed || 0)}</div>
          <div class="small">${esc(short(context.summary || "No context snapshot yet", 115))}</div>
          <div class="small muted">${esc(row.latest_at || "")}</div>
        </div>`;
      }).join("") : `<div class="muted">No captured sessions.</div>`;
      if (!selectedSession && sessions[0]) await selectSession(sessions[0].session_id);
    }

    async function selectSession(sessionId) {
      selectedSession = sessionId;
      await loadDetail();
      await loadSessions();
    }

    async function loadDetail() {
      if (!selectedSession) return;
      const data = await getJson("/api/graph/session-detail?session_id=" + encodeURIComponent(selectedSession) + "&limit=160");
      renderContext(data.current_context || {});
      renderTimeline(data.timeline || []);
      renderWindows(data.windows || []);
      renderMergePreview(data.merge_preview || {});
      renderGraph("sessionGraph", "sessionGraphStats", "sessionGraphList", data.graph || {});
      renderGraph("centralGraph", "centralStats", "centralList", data.central_graph || {});
    }

    function renderContext(context) {
      const node = (context.nodes || [])[0] || {};
      const meta = node.metadata || {};
      document.getElementById("contextBox").innerHTML = node.id ? `
        <div class="row"><strong>${esc(node.id)}</strong><span class="pill good">${esc(node.status)}</span></div>
        <pre>${esc(context.context || "")}</pre>
        <div class="grid">
          <div class="span-4"><div class="label">Changed Files</div><pre>${esc((meta.changed_files || []).join("\n"))}</pre></div>
          <div class="span-4"><div class="label">Evidence Refs</div><pre>${esc((meta.evidence_ids || []).join("\n"))}</pre></div>
          <div class="span-4"><div class="label">Trigger</div><pre>${esc(pretty(meta.trigger || {}))}</pre></div>
        </div>
      ` : `<div class="muted">No clean session context yet. Drain a write/test/git/finalize window.</div>`;
    }

    function renderTimeline(rows) {
      document.getElementById("timeline").innerHTML = rows.length ? rows.slice().reverse().map(row => `
        <details class="item">
          <summary><span class="pill">${esc(row.event_name)}</span> ${esc(short(row.summary, 120))}</summary>
          <div class="small muted">id=${esc(row.id)} at ${esc(row.created_at)}</div>
          <div class="small">tool=${esc(row.tool || "")}</div>
          <pre>${esc(pretty(row))}</pre>
        </details>
      `).join("") : `<div class="muted">No captured events for selected session.</div>`;
    }

    function renderWindows(rows) {
      document.getElementById("windows").innerHTML = rows.length ? rows.map(row => `
        <div class="item">
          <div class="row">
            <strong>Window #${esc(row.index)}</strong>
            <span class="pill ${row.status === "processed" ? "good" : "warn"}">${esc(row.status)}</span>
          </div>
          <div class="small muted">trigger=${esc((row.trigger || {}).trigger_type)} | evidence=${esc((row.evidence_ids || []).join(", "))}</div>
          <details open><summary>Cleaned evidence sent to Qwen</summary><pre>${esc(pretty(row.cleaned_evidence || []))}</pre></details>
          <details><summary>Graph nodes created from this window (${(row.graph_nodes || []).length})</summary><pre>${esc(pretty(row.graph_nodes || []))}</pre></details>
        </div>
      `).join("") : `<div class="muted">No trigger windows reconstructed yet.</div>`;
    }

    function renderMergePreview(plan) {
      const promotions = plan.planned_promotions || [];
      const relations = plan.relations || [];
      const review = plan.review_candidates || [];
      document.getElementById("mergePreview").innerHTML = `
        <div class="row">
          <span class="pill">commit ${esc(short(plan.commit_id || "HEAD", 18))}</span>
          <span class="pill">${esc(promotions.length)} promotions</span>
          <span class="pill">${esc(relations.length)} version edges</span>
          <span class="pill ${review.length ? "warn" : "good"}">${esc(review.length)} review candidates</span>
        </div>
        <div class="grid">
          <div class="span-4"><div class="label">Promote</div><pre>${esc(pretty(promotions))}</pre></div>
          <div class="span-4"><div class="label">Version Edges</div><pre>${esc(pretty(relations))}</pre></div>
          <div class="span-4"><div class="label">Skipped Support / Review</div><pre>${esc(pretty({skipped: plan.skipped || [], review_candidates: review}))}</pre></div>
        </div>
      `;
    }

    function renderGraph(svgId, statsId, listId, graph) {
      const nodes = graph.nodes || [];
      const edges = graph.edges || [];
      document.getElementById(statsId).textContent = `${nodes.length} nodes | ${edges.length} edges`;
      drawGraph(svgId, nodes, edges);
      document.getElementById(listId).innerHTML = nodes.slice(0, 16).map(node => `
        <div class="item">
          <div class="row"><strong>${esc(node.kind)}</strong><span class="pill">${esc(node.status)}</span></div>
          <div class="small">${esc(short(node.summary || node.label, 150))}</div>
          <div class="small muted">${esc(short(node.id, 110))}</div>
        </div>
      `).join("") || `<div class="muted">No graph nodes yet.</div>`;
    }

    function drawGraph(svgId, nodes, edges) {
      const svg = document.getElementById(svgId);
      const width = svg.clientWidth || 640;
      const height = 360;
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      const shown = nodes.slice(0, 28);
      const centerX = width / 2;
      const centerY = height / 2;
      const radius = Math.max(90, Math.min(width, height) / 2 - 44);
      const pos = new Map();
      shown.forEach((node, i) => {
        const angle = (Math.PI * 2 * i) / Math.max(1, shown.length);
        pos.set(node.id, { x: centerX + Math.cos(angle) * radius, y: centerY + Math.sin(angle) * radius, node });
      });
      const color = kind => ({
        ContextSnapshot: "#b9f66b", WorkChange: "#8ecae6", Decision: "#ffd166", Fix: "#72efdd",
        Bug: "#ff6b6b", TestRun: "#c8b6ff", File: "#9ab1a4", GitCommit: "#f4a261", RawEvidenceRef: "#6c757d"
      }[kind] || "#d8f3dc");
      const lines = edges.filter(edge => pos.has(edge.source_id) && pos.has(edge.target_id)).map(edge => {
        const a = pos.get(edge.source_id); const b = pos.get(edge.target_id);
        return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#2f463a" stroke-width="1.3"><title>${esc(edge.kind)}</title></line>`;
      }).join("");
      const dots = shown.map(node => {
        const p = pos.get(node.id);
        return `<g><circle class="node-dot" cx="${p.x}" cy="${p.y}" r="8" fill="${color(node.kind)}"><title>${esc(node.kind + ": " + (node.summary || node.label))}</title></circle>
        <text x="${p.x + 11}" y="${p.y + 4}" fill="#f1f8ef" font-size="10">${esc(short(node.kind, 16))}</text></g>`;
      }).join("");
      svg.innerHTML = lines + dots;
    }

    async function drainSelected() {
      if (!selectedSession) return;
      const data = await getJson("/graph/drain", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({session_id: selectedSession, limit: 5})
      });
      document.getElementById("retrieval").textContent = pretty(data);
      await loadDetail();
    }

    async function runRetrieval() {
      const query = document.getElementById("query").value.trim();
      if (!query) return;
      const data = await getJson("/graph/search", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({query, limit: 8})
      });
      document.getElementById("retrieval").textContent = pretty(data);
    }

    async function refreshAll() {
      await loadSessions();
      if (selectedSession) await loadDetail();
    }

    renderPipeline();
    refreshAll().catch(err => { document.getElementById("contextBox").textContent = "Load error: " + err.message; });
    setInterval(() => { if (selectedSession) loadDetail().catch(() => {}); }, 15000);
  </script>
</body>
</html>
"""


GRAPH3D_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AMO 3D Final Graph</title>
  <style>
    :root {
      --bg: #070b0a;
      --panel: rgba(15, 24, 21, 0.91);
      --panel-2: #16231d;
      --ink: #eef8ec;
      --muted: #9cb3a6;
      --line: #2c4539;
      --accent: #b9f66b;
      --blue: #8ecae6;
      --warn: #ffd166;
      --bad: #ff6b6b;
      --shadow: rgba(0, 0, 0, 0.46);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      overflow: hidden;
      color: var(--ink);
      background:
        radial-gradient(circle at 15% 12%, rgba(185, 246, 107, 0.18), transparent 30rem),
        radial-gradient(circle at 82% 4%, rgba(142, 202, 230, 0.12), transparent 34rem),
        linear-gradient(140deg, #070b0a 0%, #0d1512 54%, #070b0a 100%);
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }
    canvas {
      width: 100vw;
      height: 100vh;
      display: block;
      cursor: grab;
    }
    canvas.dragging { cursor: grabbing; }
    .topbar {
      position: fixed;
      inset: 16px 16px auto 16px;
      display: flex;
      justify-content: space-between;
      gap: 14px;
      pointer-events: none;
      z-index: 2;
    }
    .panel {
      pointer-events: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 24px 80px var(--shadow);
      backdrop-filter: blur(16px);
    }
    .brand { padding: 14px 16px; max-width: 760px; }
    h1 { margin: 0; font-size: 20px; letter-spacing: -0.04em; }
    .subtitle { color: var(--muted); margin-top: 5px; }
    .controls {
      padding: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
      max-width: 720px;
    }
    button, input, select {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--ink);
      border-radius: 11px;
      padding: 8px 10px;
      font: inherit;
    }
    button { cursor: pointer; color: var(--accent); }
    button.secondary { color: var(--blue); }
    input { min-width: 210px; }
    label.check {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 11px;
      padding: 8px 10px;
      color: var(--ink);
    }
    label.check input { min-width: 0; width: auto; }
    .side {
      position: fixed;
      top: 118px;
      right: 16px;
      bottom: 16px;
      width: min(430px, calc(100vw - 32px));
      padding: 14px;
      overflow: auto;
      z-index: 2;
    }
    .legend {
      position: fixed;
      left: 16px;
      bottom: 16px;
      width: min(520px, calc(100vw - 32px));
      padding: 12px;
      z-index: 2;
    }
    .explain {
      position: fixed;
      left: 16px;
      top: 182px;
      width: min(520px, calc(100vw - 32px));
      padding: 12px;
      z-index: 2;
    }
    .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .pill {
      display: inline-block;
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--blue);
      font-size: 12px;
      white-space: nowrap;
    }
    .dot {
      width: 10px;
      height: 10px;
      display: inline-block;
      border-radius: 50%;
      margin-right: 7px;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .warnbox {
      border: 1px solid rgba(255, 209, 102, 0.42);
      color: var(--warn);
      border-radius: 12px;
      padding: 9px 10px;
      margin-top: 8px;
      background: rgba(255, 209, 102, 0.07);
    }
    .kv {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 5px 10px;
      margin-top: 8px;
    }
    .kv span:nth-child(even) { color: var(--accent); }
    .meaning { color: var(--blue); margin-top: 8px; }
    .item { border-top: 1px solid rgba(44, 69, 57, 0.72); padding: 10px 0; }
    .item:first-child { border-top: 0; padding-top: 0; }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: rgba(3, 7, 6, 0.74);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      max-height: 330px;
      overflow: auto;
    }
    .hidden { display: none; }
    @media (max-width: 980px) {
      body { overflow: auto; }
      canvas { height: 70vh; }
      .topbar, .side, .legend, .explain { position: static; margin: 12px; width: auto; }
      .topbar { flex-direction: column; }
      input { min-width: 0; width: 100%; }
    }
  </style>
</head>
<body>
  <canvas id="graphCanvas" aria-label="3D central AMO graph"></canvas>
  <div class="topbar">
    <div class="brand panel">
      <h1>AMO 3D Final Graph</h1>
      <div class="subtitle">Interactive spherical graph from <code>/api/graph/central</code>. Drag to rotate, Ctrl-drag to roll, Shift-drag/pan mode to move, wheel/buttons to zoom, click a node for its flow.</div>
    </div>
    <div class="controls panel">
      <input id="search" placeholder="Filter node text, file, commit, session" />
      <select id="kindFilter"><option value="">All kinds</option></select>
      <select id="statusFilter">
        <option value="">All status</option>
        <option value="active">active</option>
        <option value="committed">committed</option>
        <option value="draft">draft</option>
      </select>
      <label class="check"><input id="connectedOnly" type="checkbox" checked /> connected only</label>
      <label class="check"><input id="showLabels" type="checkbox" checked /> labels</label>
      <label class="check"><input id="focusFlow" type="checkbox" checked /> highlight selected flow</label>
      <label class="check"><input id="showSphere" type="checkbox" checked /> sphere</label>
      <label class="check"><input id="panMode" type="checkbox" /> pan mode</label>
      <input id="limit" type="number" min="10" max="500" value="300" title="Node limit" />
      <button onclick="loadGraph()">Reload</button>
      <button class="secondary" onclick="zoomBy(1.35)">Zoom In</button>
      <button class="secondary" onclick="zoomBy(1 / 1.35)">Zoom Out</button>
      <button class="secondary" onclick="resetCamera()">Reset View</button>
      <a href="/sessions"><button class="secondary">Sessions</button></a>
    </div>
  </div>
  <aside class="side panel">
    <div class="row">
      <strong>Selected Node</strong>
      <span id="stats" class="pill">loading</span>
    </div>
    <div id="selected" class="item muted">Loading central graph...</div>
    <div class="item">
      <strong>Selected Flow Edges</strong>
      <div id="edgeList" class="small muted"></div>
    </div>
  </aside>
  <div class="explain panel">
    <div class="row"><strong>What This View Means</strong><span class="pill">central graph</span></div>
    <div class="small muted">Dots are graph nodes. Lines are graph edges. A useful memory graph should have meaningful lines like WorkChange -> GitCommit, WorkChange -> File, Decision -> WorkChange.</div>
    <div id="graphHealth" class="small"></div>
    <div id="kindStats" class="small"></div>
  </div>
  <div class="legend panel">
    <div class="row"><strong>Controls</strong><span class="pill">local only</span></div>
    <div class="small muted">Drag: rotate sphere | Ctrl-drag: roll | Shift-drag/pan mode: move | Wheel/buttons: zoom deep | Click: inspect flow | Labels appear as you zoom in</div>
    <div id="legendKinds" class="small" style="margin-top:8px;"></div>
  </div>
  <script>
    const canvas = document.getElementById("graphCanvas");
    const ctx = canvas.getContext("2d");
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const short = (value, n = 150) => {
      const text = String(value ?? "").replace(/\s+/g, " ");
      return text.length > n ? text.slice(0, n - 3) + "..." : text;
    };
    const pretty = value => JSON.stringify(value ?? {}, null, 2);
    const colors = {
      ContextSnapshot: "#b9f66b",
      WorkChange: "#8ecae6",
      Decision: "#ffd166",
      Fix: "#72efdd",
      Bug: "#ff6b6b",
      TestRun: "#c8b6ff",
      File: "#9ab1a4",
      Symbol: "#f4a261",
      GitCommit: "#43aa8b",
      Repo: "#90be6d",
      Branch: "#577590",
      RawEvidenceRef: "#6c757d"
    };
    const meanings = {
      App: "Source application that produced captured evidence, for example Codex or Claude.",
      Branch: "Git branch snapshot linked to captured work.",
      Bug: "Problem extracted from a cleaned evidence window.",
      ContextSnapshot: "Latest clean summary of one session: goal, decision, changed files, tests, blocker, next step.",
      Decision: "A design or implementation decision extracted from cleaned session evidence.",
      File: "File touched by work, commit metadata, or graph ledger evidence.",
      Fix: "Fix extracted from evidence and linked to a bug or work change when available.",
      GitCommit: "Real Git commit. This is the boundary where draft session work should merge into central memory.",
      RawEvidenceRef: "Pointer to raw captured JSONL. This is provenance, not answer-grade memory by default.",
      Repo: "Local Git repository root observed from captured work.",
      Symbol: "Code symbol extracted from a file or work change.",
      TestRun: "Test/check result extracted from cleaned evidence.",
      WorkChange: "Summarized code or repo change extracted from a trigger window."
    };
    let rawNodes = [];
    let rawEdges = [];
    let nodes = [];
    let edges = [];
    let selected = null;
    let hovered = null;
    let dragging = false;
    let dragMode = "rotate";
    let dragDistance = 0;
    let dragStart = {x: 0, y: 0};
    let lastMouse = {x: 0, y: 0};
    let camera = {rx: -0.62, ry: 0.78, rz: 0.18, zoom: 1.0, panX: 0, panY: 0};
    let sphereRadius = 320;

    function resize() {
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(canvas.clientWidth * ratio);
      canvas.height = Math.floor(canvas.clientHeight * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw();
    }

    function nodeText(node) {
      return [node.id, node.kind, node.label, node.summary, node.status, node.session_id, node.commit_id, node.evidence_id].join(" ").toLowerCase();
    }

    function kindColor(kind) {
      return colors[kind] || "#d8f3dc";
    }

    async function loadGraph() {
      const limit = Math.max(10, Math.min(500, Number(document.getElementById("limit").value || 300)));
      document.getElementById("stats").textContent = "loading";
      const response = await fetch("/api/graph/central?limit=" + encodeURIComponent(limit));
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
      rawNodes = (data.nodes || []).map((node, index) => ({...node, _i: index}));
      rawEdges = data.edges || [];
      buildKindFilter();
      applyFilters();
    }

    function buildKindFilter() {
      const select = document.getElementById("kindFilter");
      const current = select.value;
      const kinds = [...new Set(rawNodes.map(node => node.kind).filter(Boolean))].sort();
      select.innerHTML = '<option value="">All kinds</option>' + kinds.map(kind => `<option value="${esc(kind)}">${esc(kind)}</option>`).join("");
      select.value = kinds.includes(current) ? current : "";
      document.getElementById("legendKinds").innerHTML = kinds.map(kind => `<span style="margin-right:14px;"><span class="dot" style="background:${kindColor(kind)}"></span>${esc(kind)}</span>`).join("");
    }

    function applyFilters() {
      const query = document.getElementById("search").value.trim().toLowerCase();
      const kind = document.getElementById("kindFilter").value;
      const status = document.getElementById("statusFilter").value;
      const connectedOnly = document.getElementById("connectedOnly").checked;
      const rawConnectedIds = new Set();
      rawEdges.forEach(edge => {
        rawConnectedIds.add(edge.source_id);
        rawConnectedIds.add(edge.target_id);
      });
      nodes = rawNodes.filter(node => {
        if (kind && node.kind !== kind) return false;
        if (status && node.status !== status) return false;
        if (query && !nodeText(node).includes(query)) return false;
        if (connectedOnly && rawEdges.length && !rawConnectedIds.has(node.id)) return false;
        return true;
      });
      const ids = new Set(nodes.map(node => node.id));
      edges = rawEdges.filter(edge => ids.has(edge.source_id) && ids.has(edge.target_id));
      renderHealth();
      layout3d();
      selected = nodes[0] || null;
      renderSelected();
      draw();
    }

    function countBy(rows, key) {
      return rows.reduce((acc, row) => {
        const value = row[key] || "unknown";
        acc[value] = (acc[value] || 0) + 1;
        return acc;
      }, {});
    }

    function renderHealth() {
      const connectedIds = new Set();
      rawEdges.forEach(edge => {
        connectedIds.add(edge.source_id);
        connectedIds.add(edge.target_id);
      });
      const loadedIds = new Set(rawNodes.map(node => node.id));
      const connected = rawNodes.filter(node => connectedIds.has(node.id)).length;
      const isolated = rawNodes.filter(node => !connectedIds.has(node.id)).length;
      const danglingEdges = rawEdges.filter(edge => !loadedIds.has(edge.source_id) || !loadedIds.has(edge.target_id)).length;
      const edgeWarning = rawEdges.length < Math.max(3, Math.floor(rawNodes.length * 0.08));
      document.getElementById("graphHealth").innerHTML = `
        <div class="kv">
          <span>Total loaded nodes</span><span>${esc(rawNodes.length)}</span>
          <span>Total loaded edges</span><span>${esc(rawEdges.length)}</span>
          <span>Connected nodes</span><span>${esc(connected)}</span>
          <span>Isolated nodes</span><span>${esc(isolated)}</span>
          <span>Edges missing endpoint nodes</span><span>${esc(danglingEdges)}</span>
          <span>Visible after filters</span><span>${esc(nodes.length)} nodes / ${esc(edges.length)} edges</span>
        </div>
        ${edgeWarning ? `<div class="warnbox">This graph is sparse. The UI is not hiding meaning; the central graph currently has few relationships. More commit merge/work-ledger edges are needed for a rich graph.</div>` : ""}
        ${danglingEdges ? `<div class="warnbox">Some edge endpoint nodes are outside this API page limit. Raise the limit or improve central graph endpoint expansion.</div>` : ""}
      `;
      const kinds = countBy(rawNodes, "kind");
      document.getElementById("kindStats").innerHTML = Object.entries(kinds)
        .sort((a, b) => b[1] - a[1])
        .map(([kind, count]) => `<span style="margin-right:12px;"><span class="dot" style="background:${kindColor(kind)}"></span>${esc(kind)} ${esc(count)}</span>`)
        .join("");
    }

    function layout3d() {
      const map = new Map(nodes.map((node, index) => [node.id, index]));
      const n = Math.max(1, nodes.length);
      sphereRadius = 230 + 22 * Math.log2(n + 1);
      nodes.forEach((node, i) => {
        const theta = Math.acos(1 - 2 * (i + 0.5) / n);
        const phi = i * Math.PI * (3 - Math.sqrt(5)) + (node.kind || "").length * 0.071;
        node.x = Math.cos(phi) * Math.sin(theta) * sphereRadius;
        node.y = Math.sin(phi) * Math.sin(theta) * sphereRadius;
        node.z = Math.cos(theta) * sphereRadius;
        node.vx = 0; node.vy = 0; node.vz = 0;
        node.degree = 0;
      });
      edges.forEach(edge => {
        const a = map.get(edge.source_id);
        const b = map.get(edge.target_id);
        if (a !== undefined && b !== undefined) {
          nodes[a].degree += 1;
          nodes[b].degree += 1;
        }
      });
      const steps = Math.min(95, Math.max(28, Math.floor(5200 / Math.max(1, nodes.length))));
      for (let step = 0; step < steps; step++) {
        for (let i = 0; i < nodes.length; i++) {
          for (let j = i + 1; j < nodes.length; j++) {
            const a = nodes[i], b = nodes[j];
            let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
            let d2 = dx * dx + dy * dy + dz * dz + 50;
            const force = 520 / d2;
            a.vx += dx * force; a.vy += dy * force; a.vz += dz * force;
            b.vx -= dx * force; b.vy -= dy * force; b.vz -= dz * force;
          }
        }
        edges.forEach(edge => {
          const a = nodes[map.get(edge.source_id)];
          const b = nodes[map.get(edge.target_id)];
          if (!a || !b) return;
          const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
          const force = (dist - 150) * 0.0018;
          a.vx += dx / dist * force; a.vy += dy / dist * force; a.vz += dz / dist * force;
          b.vx -= dx / dist * force; b.vy -= dy / dist * force; b.vz -= dz / dist * force;
        });
        nodes.forEach(node => {
          node.x += node.vx; node.y += node.vy; node.z += node.vz;
          const length = Math.sqrt(node.x * node.x + node.y * node.y + node.z * node.z) || 1;
          const shell = sphereRadius / length;
          node.x = node.x * 0.82 + node.x * shell * 0.18;
          node.y = node.y * 0.82 + node.y * shell * 0.18;
          node.z = node.z * 0.82 + node.z * shell * 0.18;
          node.vx *= 0.78; node.vy *= 0.78; node.vz *= 0.78;
        });
      }
    }

    function project(node) {
      const sinY = Math.sin(camera.ry), cosY = Math.cos(camera.ry);
      const sinX = Math.sin(camera.rx), cosX = Math.cos(camera.rx);
      const sinZ = Math.sin(camera.rz), cosZ = Math.cos(camera.rz);
      let x = node.x * cosY - node.z * sinY;
      let z = node.x * sinY + node.z * cosY;
      let y = node.y * cosX - z * sinX;
      z = node.y * sinX + z * cosX;
      const rolledX = x * cosZ - y * sinZ;
      const rolledY = x * sinZ + y * cosZ;
      x = rolledX;
      y = rolledY;
      const depth = 940;
      const scale = camera.zoom * depth / (depth + z);
      const depth01 = Math.max(0, Math.min(1, (sphereRadius - z) / (sphereRadius * 2 || 1)));
      return {
        x: canvas.clientWidth / 2 + camera.panX + x * scale,
        y: canvas.clientHeight / 2 + camera.panY + y * scale,
        z,
        depth01,
        scale,
        r: Math.max(2.4, Math.min(19, (4.2 + Math.sqrt(node.degree || 0) * 2.0) * scale * (0.76 + depth01 * 0.55)))
      };
    }

    function nodeName(node) {
      const label = node.label || node.summary || node.id || "";
      if (node.kind === "File") return label.split(/[\\/]/).pop() || "File";
      if (node.kind === "GitCommit") return String(node.commit_id || label).slice(0, 9);
      if (node.kind === "Repo") return label.split(/[\\/]/).pop() || "Repo";
      if (node.kind === "Branch") return label || "Branch";
      return short(label, 28);
    }

    function selectedFlow() {
      const flowEdges = selected
        ? edges.filter(edge => edge.source_id === selected.id || edge.target_id === selected.id)
        : [];
      const flowIds = new Set();
      if (selected) flowIds.add(selected.id);
      flowEdges.forEach(edge => {
        flowIds.add(edge.source_id);
        flowIds.add(edge.target_id);
      });
      return {edges: flowEdges, ids: flowIds};
    }

    function drawArrow(a, b, color, alpha) {
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      const size = Math.max(6, Math.min(13, 9 * ((a.scale + b.scale) / 2)));
      const endX = b.x - Math.cos(angle) * 12;
      const endY = b.y - Math.sin(angle) * 12;
      ctx.fillStyle = color.replace("ALPHA", alpha);
      ctx.beginPath();
      ctx.moveTo(endX, endY);
      ctx.lineTo(endX - Math.cos(angle - 0.45) * size, endY - Math.sin(angle - 0.45) * size);
      ctx.lineTo(endX - Math.cos(angle + 0.45) * size, endY - Math.sin(angle + 0.45) * size);
      ctx.closePath();
      ctx.fill();
    }

    function drawSphereGuide() {
      if (!document.getElementById("showSphere").checked || !nodes.length) return;
      const rings = [];
      for (let lat = -60; lat <= 60; lat += 30) {
        const points = [];
        const theta = lat * Math.PI / 180;
        const r = Math.cos(theta) * sphereRadius;
        const y = Math.sin(theta) * sphereRadius;
        for (let i = 0; i <= 96; i++) {
          const phi = (i / 96) * Math.PI * 2;
          points.push(project({x: Math.cos(phi) * r, y, z: Math.sin(phi) * r}));
        }
        rings.push(points);
      }
      for (let lon = 0; lon < 180; lon += 30) {
        const points = [];
        const phi = lon * Math.PI / 180;
        for (let i = 0; i <= 96; i++) {
          const theta = ((i / 96) * Math.PI * 2) - Math.PI;
          points.push(project({
            x: Math.cos(phi) * Math.sin(theta) * sphereRadius,
            y: Math.cos(theta) * sphereRadius,
            z: Math.sin(phi) * Math.sin(theta) * sphereRadius
          }));
        }
        rings.push(points);
      }
      ctx.save();
      rings.forEach(points => {
        ctx.beginPath();
        points.forEach((p, index) => {
          if (index === 0) ctx.moveTo(p.x, p.y);
          else ctx.lineTo(p.x, p.y);
        });
        const frontness = points.reduce((sum, p) => sum + p.depth01, 0) / Math.max(1, points.length);
        ctx.strokeStyle = `rgba(185, 246, 107, ${0.035 + frontness * 0.055})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      });
      ctx.restore();
    }

    function draw() {
      if (!ctx) return;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(7, 11, 10, 0.82)";
      ctx.fillRect(0, 0, w, h);
      const projected = new Map(nodes.map(node => [node.id, project(node)]));
      const flow = selectedFlow();
      const focusFlow = document.getElementById("focusFlow").checked && selected;
      drawSphereGuide();
      ctx.lineWidth = 1;
      edges.forEach(edge => {
        const a = projected.get(edge.source_id);
        const b = projected.get(edge.target_id);
        if (!a || !b) return;
        const isFlowEdge = flow.edges.includes(edge);
        const depthAlpha = Math.max(0.12, Math.min(1, (a.depth01 + b.depth01) / 2));
        const alpha = isFlowEdge
          ? Math.max(0.76, Math.min(1, depthAlpha * 0.95))
          : focusFlow
            ? 0.13 + depthAlpha * 0.09
            : 0.16 + depthAlpha * 0.22;
        ctx.lineWidth = isFlowEdge ? 2.8 : 1;
        ctx.strokeStyle = isFlowEdge ? `rgba(185, 246, 107, ${alpha})` : `rgba(120, 158, 139, ${alpha})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        if (isFlowEdge) drawArrow(a, b, "rgba(185, 246, 107, ALPHA)", alpha);
        if ((isFlowEdge || camera.zoom >= 2.2) && edges.length <= 140 && document.getElementById("showLabels").checked) {
          ctx.fillStyle = "rgba(238, 248, 236, 0.62)";
          ctx.font = "10px ui-monospace, Menlo, Consolas, monospace";
          ctx.fillText(short(edge.kind, 18), (a.x + b.x) / 2 + 5, (a.y + b.y) / 2 - 5);
        }
      });
      const ordered = nodes.slice().sort((a, b) => project(a).z - project(b).z);
      const showLabels = document.getElementById("showLabels").checked;
      ordered.forEach(node => {
        const p = projected.get(node.id);
        const isSelected = selected && selected.id === node.id;
        const isHovered = hovered && hovered.id === node.id;
        const inFlow = flow.ids.has(node.id);
        const nodeAlpha = focusFlow && !inFlow
          ? 0.22 + p.depth01 * 0.22
          : 0.42 + p.depth01 * 0.58;
        ctx.beginPath();
        ctx.fillStyle = kindColor(node.kind);
        ctx.globalAlpha = nodeAlpha;
        ctx.arc(p.x, p.y, p.r + (isSelected ? 4 : isHovered ? 2 : 0), 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = isSelected ? "#ffffff" : inFlow ? "#b9f66b" : "#07100d";
        ctx.lineWidth = isSelected ? 2.5 : inFlow ? 2 : 1.5;
        ctx.stroke();
        const zoomLabel = camera.zoom >= 1.7 && (node.degree > 0 || nodes.length <= 45);
        const deepZoomLabel = camera.zoom >= 2.7;
        if (showLabels && (isSelected || isHovered || inFlow || zoomLabel || deepZoomLabel)) {
          ctx.fillStyle = "#eef8ec";
          ctx.font = "11px ui-monospace, Menlo, Consolas, monospace";
          ctx.fillText(`${short(node.kind, 14)}: ${short(nodeName(node), 32)}`, p.x + p.r + 5, p.y + 4);
        }
      });
      document.getElementById("stats").textContent = `${nodes.length} nodes | ${edges.length} edges`;
    }

    function hitTest(x, y) {
      let best = null;
      let bestDistance = Infinity;
      nodes.forEach(node => {
        const p = project(node);
        const dx = p.x - x, dy = p.y - y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < p.r + 8 && distance < bestDistance) {
          best = node;
          bestDistance = distance;
        }
      });
      return best;
    }

    function directionLabel(edge, node) {
      if (edge.source_id === node.id) return `${short(node.id, 48)} --${edge.kind}--> ${short(edge.target_id, 48)}`;
      return `${short(edge.source_id, 48)} --${edge.kind}--> ${short(node.id, 48)}`;
    }

    function traceForNode(node, linked) {
      const evidenceIds = new Set();
      if (node.evidence_id) evidenceIds.add(node.evidence_id);
      (node.metadata && node.metadata.evidence_ids || []).forEach(id => evidenceIds.add(id));
      linked.forEach(edge => {
        if (edge.evidence_id) evidenceIds.add(edge.evidence_id);
      });
      const trigger = node.metadata && node.metadata.trigger || {};
      const relationKinds = [...new Set(linked.map(edge => edge.kind))];
      const connectedTo = linked.map(edge => edge.source_id === node.id ? edge.target_id : edge.source_id);
      return `
        <div class="item">
          <strong>Knowledge Creation Trace</strong>
          <div class="small muted">This is how to audit why this node exists and how it became connected.</div>
          <div class="kv">
            <span>1. Raw capture refs</span><span>${esc(evidenceIds.size ? [...evidenceIds].join(", ") : "none on node")}</span>
            <span>2. Trigger window</span><span>${esc(trigger.trigger_type || "not recorded on node")}</span>
            <span>3. Extracted node</span><span>${esc(node.kind)} / ${esc(node.scope || "unknown")} / ${esc(node.status || "unknown")}</span>
            <span>4. Relation types</span><span>${esc(relationKinds.length ? relationKinds.join(", ") : "none visible")}</span>
            <span>5. Connected nodes</span><span>${esc(connectedTo.length ? connectedTo.map(id => short(id, 44)).join(", ") : "none visible")}</span>
            <span>6. Commit boundary</span><span>${esc(node.commit_id || "not linked on this node")}</span>
          </div>
        </div>
      `;
    }

    function renderSelected() {
      const box = document.getElementById("selected");
      const edgeBox = document.getElementById("edgeList");
      if (!selected) {
        box.innerHTML = '<div class="muted">No node selected.</div>';
        edgeBox.textContent = "";
        return;
      }
      const linked = edges.filter(edge => edge.source_id === selected.id || edge.target_id === selected.id).slice(0, 18);
      box.innerHTML = `
        <div class="item">
          <div class="row"><strong>${esc(selected.kind)}</strong><span class="pill">${esc(selected.status || "")}</span></div>
          <div class="meaning">${esc(meanings[selected.kind] || "Graph node stored by AMO.")}</div>
          <div>${esc(short(selected.summary || selected.label || selected.id, 260))}</div>
          <div class="small muted">${esc(selected.id)}</div>
        </div>
        <div class="item small">
          <div>session_id: ${esc(selected.session_id || "")}</div>
          <div>commit_id: ${esc(selected.commit_id || "")}</div>
          <div>evidence_id: ${esc(selected.evidence_id || "")}</div>
        </div>
        ${traceForNode(selected, linked)}
        <details class="item" open><summary>Metadata</summary><pre>${esc(pretty(selected.metadata || {}))}</pre></details>
      `;
      edgeBox.innerHTML = linked.length ? linked.map(edge => `
        <div class="item">
          <span class="pill">${esc(edge.kind)}</span>
          <div>${esc(directionLabel(edge, selected))}</div>
          <div class="muted">evidence=${esc(edge.evidence_id || "")} confidence=${esc(edge.confidence ?? "")}</div>
        </div>
      `).join("") : '<div class="muted">No visible edges for selected node.</div>';
    }

    function resetCamera() {
      camera = {rx: -0.62, ry: 0.78, rz: 0.18, zoom: 1.0, panX: 0, panY: 0};
      draw();
    }

    function zoomBy(factor) {
      camera.zoom *= factor;
      camera.zoom = Math.max(0.04, Math.min(14, camera.zoom));
      draw();
    }

    canvas.addEventListener("mousedown", event => {
      event.preventDefault();
      dragging = true;
      dragMode = event.shiftKey || event.button === 1 || document.getElementById("panMode").checked
        ? "pan"
        : event.ctrlKey
          ? "roll"
          : "rotate";
      dragDistance = 0;
      canvas.classList.add("dragging");
      dragStart = {x: event.clientX, y: event.clientY};
      lastMouse = {x: event.clientX, y: event.clientY};
    });
    window.addEventListener("mouseup", event => {
      if (!dragging) return;
      dragging = false;
      canvas.classList.remove("dragging");
      const moved = Math.abs(event.clientX - dragStart.x) + Math.abs(event.clientY - dragStart.y) + dragDistance;
      if (moved < 8) {
        selected = hitTest(event.clientX, event.clientY) || selected;
        renderSelected();
        draw();
      }
    });
    window.addEventListener("mousemove", event => {
      if (dragging) {
        const dx = event.clientX - lastMouse.x;
        const dy = event.clientY - lastMouse.y;
        dragDistance += Math.abs(dx) + Math.abs(dy);
        if (dragMode === "pan") {
          camera.panX += dx;
          camera.panY += dy;
        } else if (dragMode === "roll") {
          camera.rz += dx * 0.008;
        } else {
          camera.ry += dx * 0.006;
          camera.rx += dy * 0.006;
          camera.rx = Math.max(-1.45, Math.min(1.45, camera.rx));
        }
        lastMouse = {x: event.clientX, y: event.clientY};
        draw();
      } else {
        const next = hitTest(event.clientX, event.clientY);
        if ((next && next.id) !== (hovered && hovered.id)) {
          hovered = next;
          draw();
        }
      }
    });
    canvas.addEventListener("wheel", event => {
      event.preventDefault();
      zoomBy(event.deltaY > 0 ? 0.86 : 1.16);
    }, {passive: false});
    document.getElementById("search").addEventListener("input", applyFilters);
    document.getElementById("kindFilter").addEventListener("change", applyFilters);
    document.getElementById("statusFilter").addEventListener("change", applyFilters);
    document.getElementById("connectedOnly").addEventListener("change", applyFilters);
    document.getElementById("showLabels").addEventListener("change", draw);
    document.getElementById("focusFlow").addEventListener("change", draw);
    window.addEventListener("resize", resize);
    resize();
    loadGraph().catch(err => {
      document.getElementById("selected").innerHTML = `<div class="muted">Graph load error: ${esc(err.message)}</div>`;
      document.getElementById("stats").textContent = "error";
    });
  </script>
</body>
</html>
"""


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AMO Local Memory Dashboard</title>
  <style>
    :root {
      --bg: #0f1411;
      --panel: #17211b;
      --panel-2: #1e2b23;
      --ink: #edf7ee;
      --muted: #9db3a4;
      --line: #314236;
      --accent: #9fe870;
      --warn: #ffd166;
      --bad: #ff6b6b;
      --blue: #8ecae6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(159, 232, 112, 0.16), transparent 34rem),
        linear-gradient(135deg, #0f1411 0%, #111b18 55%, #0d1110 100%);
      font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }
    header {
      padding: 24px 28px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
    }
    h1 { margin: 0; font-size: 22px; letter-spacing: -0.03em; }
    .subtitle { color: var(--muted); margin-top: 6px; max-width: 760px; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    button, input {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--ink);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
    }
    button { cursor: pointer; color: var(--accent); }
    input { min-width: 320px; }
    main { padding: 22px 28px 42px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .card {
      background: rgba(23, 33, 27, 0.9);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      min-width: 0;
      box-shadow: 0 18px 60px rgba(0,0,0,0.24);
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-7 { grid-column: span 7; }
    .span-12 { grid-column: span 12; }
    h2 { font-size: 13px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.12em; margin: 0 0 12px; }
    .metric { font-size: 28px; color: var(--accent); margin: 2px 0; }
    .label { color: var(--muted); font-size: 12px; }
    .item {
      padding: 11px 0;
      border-top: 1px solid rgba(49, 66, 54, 0.7);
    }
    .item:first-child { border-top: 0; padding-top: 0; }
    .row { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
    .pill {
      display: inline-block;
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--blue);
      font-size: 12px;
      white-space: nowrap;
    }
    .pill.active { color: var(--accent); }
    .pill.superseded { color: var(--warn); }
    .text { color: var(--ink); overflow-wrap: anywhere; }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #0b100d;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      max-height: 360px;
      overflow: auto;
    }
    .clickable { cursor: pointer; }
    .clickable:hover { color: var(--accent); }
    @media (max-width: 980px) {
      .span-3, .span-4, .span-5, .span-6, .span-7 { grid-column: span 12; }
      header { flex-direction: column; }
      input { min-width: 0; width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Agent Memory Orchestrator</h1>
      <div class="subtitle">Local dashboard for watching Claude/Codex events, extracted memory, retrieval queries, and returned candidate context. Served from the local daemon only.</div>
    </div>
    <div class="toolbar">
      <input id="query" placeholder="Search memory, e.g. why retry jitter changed" />
      <button onclick="runSearch()">Search</button>
      <button onclick="loadDashboard()">Refresh</button>
    </div>
  </header>
  <main>
    <section id="metrics" class="grid"></section>
    <section class="grid" style="margin-top:16px">
      <div class="card span-4"><h2>Sessions</h2><div id="sessions"></div></div>
      <div class="card span-4"><h2>Recent Events</h2><div id="events"></div></div>
      <div class="card span-4"><h2>Recent Memories</h2><div id="memories"></div></div>
      <div class="card span-5"><h2>Retrieval Runs</h2><div id="retrievalRuns"></div></div>
      <div class="card span-7"><h2>Selected Retrieval Detail</h2><div id="retrievalDetail" class="muted">Select a retrieval run to see what was asked and what memory returned.</div></div>
      <div class="card span-12"><h2>Search Result</h2><pre id="searchResult">No manual search yet.</pre></div>
    </section>
  </main>
  <script>
    const fmt = value => value === null || value === undefined ? "" : String(value);
    const esc = value => fmt(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const short = (value, n = 180) => {
      const text = fmt(value).replace(/\s+/g, " ");
      return text.length > n ? text.slice(0, n - 3) + "..." : text;
    };
    let dashboardLoading = false;

    async function getJson(url, options) {
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
      return data;
    }

    async function loadDashboard() {
      if (dashboardLoading) return;
      dashboardLoading = true;
      try {
        const { data } = await getJson("/api/dashboard?limit=30");
        renderMetrics(data.metrics.counts || {});
        renderSessions(data.sessions || []);
        renderEvents(data.recent_events || []);
        renderMemories(data.recent_memories || []);
        renderRetrievalRuns(data.retrieval_runs || []);
      } catch (err) {
        document.getElementById("searchResult").textContent = "Dashboard error: " + err.message;
      } finally {
        dashboardLoading = false;
      }
    }

    function renderMetrics(counts) {
      const keys = ["sessions", "events", "chunks", "memory_units", "retrieval_runs", "retrieval_candidates", "kg_edges", "consolidation_decisions"];
      document.getElementById("metrics").innerHTML = keys.map(key => `
        <div class="card span-3">
          <div class="label">${esc(key)}</div>
          <div class="metric">${esc(counts[key] ?? 0)}</div>
        </div>
      `).join("");
    }

    function renderSessions(rows) {
      document.getElementById("sessions").innerHTML = rows.length ? rows.map(row => `
        <div class="item">
          <div class="row"><strong>${esc(row.title || row.id)}</strong><span class="pill">${esc(row.status)}</span></div>
          <div class="small muted">${esc(row.id)} · events ${esc(row.event_count)} · memories ${esc(row.memory_count)}</div>
          <div class="small text">${esc(short(row.summary_text || "No summary yet.", 220))}</div>
        </div>
      `).join("") : `<div class="muted">No sessions yet.</div>`;
    }

    function renderEvents(rows) {
      document.getElementById("events").innerHTML = rows.length ? rows.map(row => `
        <div class="item">
          <div class="row"><strong>${esc(row.agent)}</strong><span class="pill">${esc(row.event_type)}</span></div>
          <div class="small muted">${esc(row.session_id)} · ${esc(row.created_at)} ${row.redacted ? "· redacted" : ""}</div>
          <div class="text">${esc(short(row.content_preview || row.content, 240))}</div>
        </div>
      `).join("") : `<div class="muted">No events yet.</div>`;
    }

    function renderMemories(rows) {
      document.getElementById("memories").innerHTML = rows.length ? rows.map(row => `
        <div class="item">
          <div class="row"><strong>${esc(row.memory_type)}</strong><span class="pill ${esc(row.status)}">${esc(row.status)}</span></div>
          <div class="small muted">${esc(row.subject)} · confidence ${esc(row.confidence)} · topic ${esc(row.topic_key)}</div>
          <div class="text">${esc(short(row.summary_preview || row.summary, 260))}</div>
        </div>
      `).join("") : `<div class="muted">No memories yet.</div>`;
    }

    function renderRetrievalRuns(rows) {
      document.getElementById("retrievalRuns").innerHTML = rows.length ? rows.map(row => `
        <div class="item clickable" onclick="loadRetrievalDetail('${esc(row.id)}')">
          <div class="row"><strong>${esc(short(row.query, 80))}</strong><span class="pill">${esc(row.intent)}</span></div>
          <div class="small muted">${esc(row.id)} · ${esc(row.candidate_count)} candidates · ${esc(row.duration_ms)}ms</div>
        </div>
      `).join("") : `<div class="muted">No retrieval runs yet.</div>`;
    }

    async function loadRetrievalDetail(id) {
      try {
        const { detail } = await getJson("/api/retrieval-runs/" + encodeURIComponent(id));
        const run = detail.run;
        const candidates = detail.candidates || [];
        document.getElementById("retrievalDetail").innerHTML = `
          <div class="item">
            <div class="row"><strong>Asked: ${esc(run.query)}</strong><span class="pill">${esc(run.intent)}</span></div>
            <div class="small muted">session ${esc(run.session_id || "all")} · ${esc(run.status)} · ${esc(run.duration_ms)}ms</div>
          </div>
          ${candidates.map(c => `
            <div class="item">
              <div class="row"><strong>#${esc(c.rank)} ${esc(c.memory_type)} · ${esc(c.subject)}</strong><span class="pill ${esc(c.status)}">${esc(c.final_score)}</span></div>
              <div class="small muted">source ${esc(c.source)} · rrf ${esc(c.rrf_score)} · rerank ${esc(c.rerank_score)} · memory ${esc(c.memory_id)}</div>
              <div class="text">${esc(short(c.summary_preview || c.summary, 320))}</div>
            </div>
          `).join("") || `<div class="muted">No returned candidates.</div>`}
        `;
      } catch (err) {
        document.getElementById("retrievalDetail").textContent = "Retrieval detail error: " + err.message;
      }
    }

    async function runSearch() {
      const query = document.getElementById("query").value.trim();
      if (!query) return;
      try {
        const data = await getJson("/memory/search", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({query, limit: 8})
        });
        document.getElementById("searchResult").textContent = JSON.stringify(data.results, null, 2);
        await loadDashboard();
      } catch (err) {
        document.getElementById("searchResult").textContent = "Search error: " + err.message;
      }
    }

    loadDashboard();
    setInterval(loadDashboard, 10000);
  </script>
</body>
</html>
"""


GRAPH_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AMO Knowledge Graph</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #111a24;
      --panel-2: #172232;
      --ink: #e6edf3;
      --muted: #8b949e;
      --line: #30363d;
      --entity: #79c0ff;
      --memory: #a5d6ff;
      --file: #7ee787;
      --type: #d2a8ff;
      --edge: #6e7681;
      --accent: #ffa657;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 20% 0%, rgba(121, 192, 255, 0.16), transparent 32rem),
        linear-gradient(145deg, #0d1117 0%, #101923 54%, #0a0d12 100%);
      font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }
    header {
      padding: 22px 26px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
    }
    h1 { margin: 0; font-size: 22px; letter-spacing: -0.03em; }
    .subtitle { color: var(--muted); margin-top: 6px; max-width: 820px; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    input, button, label {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--ink);
      border-radius: 10px;
      padding: 9px 11px;
      font: inherit;
    }
    input { min-width: 280px; }
    button { color: var(--accent); cursor: pointer; }
    label { color: var(--muted); display: flex; gap: 8px; align-items: center; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 390px;
      gap: 14px;
      padding: 16px;
      height: calc(100vh - 92px);
    }
    .panel {
      background: rgba(17, 26, 36, 0.92);
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      min-width: 0;
      box-shadow: 0 18px 70px rgba(0,0,0,0.28);
    }
    #graphWrap { position: relative; }
    canvas { width: 100%; height: 100%; display: block; cursor: grab; }
    canvas.dragging { cursor: grabbing; }
    .side { padding: 16px; overflow: auto; }
    h2 { font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.12em; margin: 0 0 12px; }
    .statgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 16px; }
    .stat { border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: #0b1220; }
    .stat strong { display: block; font-size: 22px; color: var(--accent); }
    .muted { color: var(--muted); }
    .item { border-top: 1px solid var(--line); padding: 11px 0; }
    .item:first-child { border-top: 0; padding-top: 0; }
    .pill {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--entity);
      font-size: 12px;
      margin: 2px 4px 2px 0;
    }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink); }
    @media (max-width: 980px) {
      header { flex-direction: column; }
      main { grid-template-columns: 1fr; height: auto; }
      #graphWrap { height: 620px; }
      input { min-width: 0; width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AMO Knowledge Graph</h1>
      <div class="subtitle">SQLite-backed graph view over <code>entities</code>, <code>kg_edges</code>, and evidence memories. This is local visualization, not an external graph database.</div>
    </div>
    <div class="toolbar">
      <input id="query" placeholder="Filter graph, e.g. codex hooks" />
      <input id="session" placeholder="Optional session_id" />
      <input id="relation" placeholder="relation, e.g. supersedes" style="min-width:190px" />
      <input id="memoryType" placeholder="memory_type" style="min-width:140px" />
      <input id="nodeType" placeholder="node_type" style="min-width:120px" />
      <input id="minConfidence" type="number" step="0.05" min="0" max="1" placeholder="min conf" style="min-width:92px;width:92px" />
      <input id="limit" type="number" value="100" min="10" max="500" style="min-width:90px;width:90px" />
      <label><input id="historical" type="checkbox" /> history</label>
      <button onclick="loadGraph()">Load Graph</button>
      <button id="spinButton" onclick="toggleSpin()">Pause Spin</button>
      <button onclick="resetView()">Reset 3D</button>
      <button onclick="location.href='/'">Dashboard</button>
    </div>
  </header>
  <main>
    <section id="graphWrap" class="panel">
      <canvas id="graph3d" role="img" aria-label="3D knowledge graph visualization"></canvas>
    </section>
    <aside class="panel side">
      <h2>Graph Stats</h2>
      <div id="stats" class="statgrid"></div>
      <h2>Selected Node</h2>
      <div id="selected" class="muted">Click a node to inspect its metadata and evidence.</div>
      <h2 style="margin-top:18px">Relations</h2>
      <div id="relations"></div>
    </aside>
  </main>
  <script>
    const canvas = document.getElementById("graph3d");
    const ctx = canvas.getContext("2d");
    const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const palette = {
      ink: css("--ink"),
      muted: css("--muted"),
      edge: css("--edge"),
      accent: css("--accent"),
      entity: css("--entity"),
      memory: css("--memory"),
      file: css("--file"),
      type: css("--type")
    };
    let graphState = {
      nodes: [],
      edges: [],
      nodeMap: new Map(),
      rotationX: -0.25,
      rotationY: 0.6,
      zoom: 1,
      autoRotate: true,
      dragging: false,
      dragStart: null,
      hovered: null,
      projected: []
    };
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const short = (value, n = 90) => {
      const text = String(value ?? "").replace(/\s+/g, " ");
      return text.length > n ? text.slice(0, n - 3) + "..." : text;
    };
    const colorFor = type => ({
      file: palette.file,
      memory: palette.memory,
      memory_type: palette.type,
      topic: palette.entity
    })[type] || palette.entity;
    const radiusFor = node => node.type === "memory" ? 15 : node.type === "file" ? 13 : node.type === "memory_type" ? 10 : 12;

    async function getJson(url) {
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
      return data;
    }

    async function loadGraph() {
      const params = new URLSearchParams();
      const query = document.getElementById("query").value.trim();
      const session = document.getElementById("session").value.trim();
      const relation = document.getElementById("relation").value.trim();
      const memoryType = document.getElementById("memoryType").value.trim();
      const nodeType = document.getElementById("nodeType").value.trim();
      const minConfidence = document.getElementById("minConfidence").value.trim();
      const limit = document.getElementById("limit").value || "100";
      if (query) params.set("query", query);
      if (session) params.set("session_id", session);
      if (relation) params.set("relation", relation);
      if (memoryType) params.set("memory_type", memoryType);
      if (nodeType) params.set("node_type", nodeType);
      if (minConfidence) params.set("min_confidence", minConfidence);
      params.set("limit", limit);
      params.set("include_historical", document.getElementById("historical").checked ? "true" : "false");
      try {
        const { graph } = await getJson("/api/graph?" + params.toString());
        renderStats(graph.stats || {});
        renderRelations((graph.stats || {}).relation_counts || {});
        renderGraph(graph.nodes || [], graph.edges || []);
      } catch (err) {
        document.getElementById("selected").innerHTML = `<span class="muted">Graph error: ${esc(err.message)}</span>`;
      }
    }

    function renderStats(stats) {
      document.getElementById("stats").innerHTML = `
        <div class="stat"><strong>${esc(stats.node_count || 0)}</strong><span class="muted">nodes</span></div>
        <div class="stat"><strong>${esc(stats.edge_count || 0)}</strong><span class="muted">edges</span></div>
        <div class="stat"><strong>${esc(stats.evidence_memory_count || 0)}</strong><span class="muted">memories</span></div>
      `;
    }

    function renderRelations(counts) {
      const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
      document.getElementById("relations").innerHTML = entries.length
        ? entries.map(([key, value]) => `<span class="pill">${esc(key)}: ${esc(value)}</span>`).join("")
        : `<div class="muted">No relations loaded.</div>`;
    }

    function renderGraph(nodes, edges) {
      const degree = new Map(nodes.map(n => [n.id, 0]));
      for (const edge of edges) {
        degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
        degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
      }
      const ordered = nodes
        .map(n => ({...n, degree: degree.get(n.id) || 0}))
        .sort((a, b) => b.degree - a.degree);
      const sphereRadius = Math.max(180, Math.min(520, 90 + Math.sqrt(Math.max(1, ordered.length)) * 42));
      ordered.forEach((node, index) => {
        const t = ordered.length <= 1 ? 0.5 : index / (ordered.length - 1);
        const theta = index * 2.399963229728653;
        const z = sphereRadius * (1 - 2 * t);
        const radial = Math.sqrt(Math.max(0, sphereRadius * sphereRadius - z * z));
        const hubPull = Math.max(0.35, 1 - Math.min(0.45, node.degree * 0.025));
        node.x = Math.cos(theta) * radial * hubPull;
        node.y = Math.sin(theta) * radial * hubPull;
        node.z = z * hubPull;
        node.vx = 0; node.vy = 0; node.vz = 0;
      });
      runLayout(ordered, edges);
      graphState.nodes = ordered;
      graphState.edges = edges;
      graphState.nodeMap = new Map(ordered.map(n => [n.id, n]));
      graphState.hovered = null;
      if (!ordered.length) {
        document.getElementById("selected").innerHTML = `<div class="muted">No graph rows matched this filter.</div>`;
      }
      drawGraph();
    }

    function runLayout(nodes, edges) {
      const nodeMap = new Map(nodes.map(n => [n.id, n]));
      const iterations = Math.min(180, Math.max(60, nodes.length * 2));
      for (let step = 0; step < iterations; step++) {
        for (let i = 0; i < nodes.length; i++) {
          for (let j = i + 1; j < nodes.length; j++) {
            const a = nodes[i], b = nodes[j];
            const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
            const dist2 = Math.max(90, dx * dx + dy * dy + dz * dz);
            const force = 5200 / dist2;
            const dist = Math.sqrt(dist2);
            const fx = (dx / dist) * force, fy = (dy / dist) * force, fz = (dz / dist) * force;
            a.vx += fx; a.vy += fy; a.vz += fz;
            b.vx -= fx; b.vy -= fy; b.vz -= fz;
          }
        }
        for (const edge of edges) {
          const a = nodeMap.get(edge.source), b = nodeMap.get(edge.target);
          if (!a || !b) continue;
          const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
          const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy + dz * dz));
          const target = edge.relation === "evidenced_by" ? 125 : 185;
          const force = (dist - target) * 0.018;
          const fx = (dx / dist) * force, fy = (dy / dist) * force, fz = (dz / dist) * force;
          a.vx += fx; a.vy += fy; a.vz += fz;
          b.vx -= fx; b.vy -= fy; b.vz -= fz;
        }
        for (const node of nodes) {
          node.vx += -node.x * 0.0016;
          node.vy += -node.y * 0.0016;
          node.vz += -node.z * 0.0016;
          node.x += node.vx;
          node.y += node.vy;
          node.z += node.vz;
          node.vx *= 0.72;
          node.vy *= 0.72;
          node.vz *= 0.72;
        }
      }
    }

    function resizeCanvas() {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.floor(rect.width * ratio));
      const height = Math.max(1, Math.floor(rect.height * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      return { width: rect.width, height: rect.height };
    }

    function rotatePoint(node) {
      const cx = Math.cos(graphState.rotationX), sx = Math.sin(graphState.rotationX);
      const cy = Math.cos(graphState.rotationY), sy = Math.sin(graphState.rotationY);
      const x1 = node.x * cy + node.z * sy;
      const z1 = -node.x * sy + node.z * cy;
      const y1 = node.y * cx - z1 * sx;
      const z2 = node.y * sx + z1 * cx;
      return { x: x1, y: y1, z: z2 };
    }

    function project(node, width, height) {
      const rotated = rotatePoint(node);
      const camera = 850;
      const scale = (camera / (camera + rotated.z)) * graphState.zoom;
      return {
        node,
        x: width / 2 + rotated.x * scale,
        y: height / 2 + rotated.y * scale,
        z: rotated.z,
        scale,
        radius: (radiusFor(node) + Math.min(9, node.degree * 0.7)) * Math.max(0.55, scale)
      };
    }

    function drawGraph() {
      const { width, height } = resizeCanvas();
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#0a0d12";
      ctx.fillRect(0, 0, width, height);
      const projected = graphState.nodes.map(node => project(node, width, height));
      graphState.projected = projected;
      const byId = new Map(projected.map(p => [p.node.id, p]));

      ctx.lineWidth = 1.2;
      for (const edge of graphState.edges) {
        const a = byId.get(edge.source), b = byId.get(edge.target);
        if (!a || !b) continue;
        const alpha = Math.max(0.18, Math.min(0.82, 0.48 + (a.z + b.z) / 2200));
        ctx.strokeStyle = rgba(palette.edge, alpha);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        if (a.scale > 0.92 && b.scale > 0.92 && graphState.edges.length <= 60) {
          ctx.fillStyle = rgba(palette.muted, alpha);
          ctx.font = "10px ui-monospace, Menlo, Consolas, monospace";
          ctx.fillText(edge.relation, (a.x + b.x) / 2, (a.y + b.y) / 2);
        }
      }

      const sorted = projected.sort((a, b) => a.z - b.z);
      for (const point of sorted) {
        const node = point.node;
        const alpha = Math.max(0.45, Math.min(1, 0.72 + point.z / 1100));
        ctx.beginPath();
        ctx.fillStyle = rgba(colorFor(node.type), alpha);
        ctx.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.lineWidth = node === graphState.hovered ? 3 : 1.5;
        ctx.strokeStyle = node === graphState.hovered ? palette.accent : "#0d1117";
        ctx.stroke();
        if (point.scale > 0.78 || node === graphState.hovered) {
          ctx.font = `${Math.max(10, 11 * point.scale)}px ui-monospace, Menlo, Consolas, monospace`;
          ctx.lineWidth = 4;
          ctx.strokeStyle = "#0d1117";
          ctx.strokeText(short(node.label, node === graphState.hovered ? 42 : 28), point.x + point.radius + 7, point.y + 4);
          ctx.fillStyle = palette.ink;
          ctx.fillText(short(node.label, node === graphState.hovered ? 42 : 28), point.x + point.radius + 7, point.y + 4);
        }
      }
    }

    function rgba(hex, alpha) {
      if (!hex.startsWith("#")) return hex;
      const value = hex.slice(1);
      const bigint = parseInt(value.length === 3 ? value.split("").map(ch => ch + ch).join("") : value, 16);
      const r = (bigint >> 16) & 255;
      const g = (bigint >> 8) & 255;
      const b = bigint & 255;
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function resetView() {
      graphState.rotationX = -0.25;
      graphState.rotationY = 0.6;
      graphState.zoom = 1;
      drawGraph();
    }

    function toggleSpin() {
      graphState.autoRotate = !graphState.autoRotate;
      document.getElementById("spinButton").textContent = graphState.autoRotate ? "Pause Spin" : "Resume Spin";
    }

    function nodeAt(event) {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const candidates = [...graphState.projected].sort((a, b) => b.z - a.z);
      return candidates.find(p => {
        const dx = x - p.x, dy = y - p.y;
        return dx * dx + dy * dy <= (p.radius + 5) * (p.radius + 5);
      })?.node || null;
    }

    function selectNode(node, edges) {
      document.getElementById("selected").innerHTML = `
        <div class="item">
          <div><strong>${esc(node.label)}</strong></div>
          <div class="muted">${esc(node.id)} · ${esc(node.type)}</div>
          ${node.memory_id ? `<div><span class="pill">${esc(node.memory_type)}</span><span class="pill">${esc(node.memory_status)}</span></div>` : ""}
          ${node.summary_preview ? `<p>${esc(node.summary_preview)}</p>` : ""}
        </div>
        <div class="item">
          <strong>Connected edges</strong>
          ${edges.map(edge => `<div class="muted">${esc(edge.source)} --${esc(edge.relation)}--> ${esc(edge.target)} · confidence ${esc(edge.confidence)}</div>`).join("") || `<div class="muted">No connected edges.</div>`}
        </div>
        <pre>${esc(JSON.stringify(node, null, 2))}</pre>
      `;
    }

    canvas.addEventListener("pointerdown", event => {
      graphState.dragging = true;
      graphState.dragStart = { x: event.clientX, y: event.clientY, rx: graphState.rotationX, ry: graphState.rotationY };
      canvas.classList.add("dragging");
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", event => {
      if (graphState.dragging && graphState.dragStart) {
        const dx = event.clientX - graphState.dragStart.x;
        const dy = event.clientY - graphState.dragStart.y;
        graphState.rotationY = graphState.dragStart.ry + dx * 0.008;
        graphState.rotationX = Math.max(-1.35, Math.min(1.35, graphState.dragStart.rx + dy * 0.008));
        drawGraph();
        return;
      }
      const hovered = nodeAt(event);
      if (hovered !== graphState.hovered) {
        graphState.hovered = hovered;
        drawGraph();
      }
    });
    canvas.addEventListener("pointerup", event => {
      graphState.dragging = false;
      graphState.dragStart = null;
      canvas.classList.remove("dragging");
      canvas.releasePointerCapture(event.pointerId);
    });
    canvas.addEventListener("click", event => {
      const node = nodeAt(event);
      if (node) selectNode(node, graphState.edges.filter(e => e.source === node.id || e.target === node.id));
    });
    canvas.addEventListener("wheel", event => {
      event.preventDefault();
      graphState.zoom = Math.max(0.35, Math.min(3.2, graphState.zoom * (event.deltaY > 0 ? 0.92 : 1.08)));
      drawGraph();
    }, { passive: false });
    window.addEventListener("resize", drawGraph);

    loadGraph();
    function animateGraph() {
      if (graphState.autoRotate && !graphState.dragging && graphState.nodes.length) {
        graphState.rotationY += 0.0022;
        drawGraph();
      }
      requestAnimationFrame(animateGraph);
    }
    requestAnimationFrame(animateGraph);
  </script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Agent Memory Orchestrator daemon")
    parser.add_argument("--amo-home", help="AMO home directory containing config.json and .data/")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    if args.amo_home:
        os.environ["AMO_HOME"] = args.amo_home
    settings = Settings.load()
    host = args.host or settings.mcp_host
    port = args.port or settings.mcp_port
    if settings.local_only and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("AMO_LOCAL_ONLY=true requires daemon host to be localhost")

    AmoHandler.settings = settings
    server = ThreadingHTTPServer((host, port), AmoHandler)
    print(f"amo-daemon listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
