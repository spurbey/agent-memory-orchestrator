from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .memory_service import MemoryService


class AmoHandler(BaseHTTPRequestHandler):
    settings: Settings

    def _write_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._write_html(200, DASHBOARD_HTML)
            return
        if path == "/health":
            self._write_json(200, {"ok": True, "service": "agent-memory-orchestrator"})
            return
        if path == "/metrics":
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                self._write_json(200, svc.inspect_metrics())
            finally:
                svc.close()
            return
        if path.startswith("/api/"):
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                limit = int((query.get("limit") or ["25"])[0])
                session_id = (query.get("session_id") or [""])[0] or None
                if path == "/api/dashboard":
                    self._write_json(200, {"ok": True, "data": svc.dashboard_snapshot(limit=limit)})
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

        svc = MemoryService(self.settings)
        try:
            svc.init_db()
            if self.path == "/hooks/ingest":
                result = svc.ingest_hook_payload(payload, default_agent=str(payload.get("agent") or "codex"))
                self._write_json(200, {"ok": True, **result})
                return
            if self.path == "/memory/search":
                result = svc.search_memories(
                    query=str(payload.get("query") or ""),
                    session_id=payload.get("session_id") or None,
                    limit=int(payload.get("limit") or 10),
                )
                self._write_json(200, {"ok": True, "results": result})
                return
            self._write_json(404, {"error": "not found"})
        finally:
            svc.close()

    def log_message(self, format: str, *args: object) -> None:
        return


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

    async function getJson(url, options) {
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
      return data;
    }

    async function loadDashboard() {
      try {
        const { data } = await getJson("/api/dashboard?limit=30");
        renderMetrics(data.metrics.counts || {});
        renderSessions(data.sessions || []);
        renderEvents(data.recent_events || []);
        renderMemories(data.recent_memories || []);
        renderRetrievalRuns(data.retrieval_runs || []);
      } catch (err) {
        document.getElementById("searchResult").textContent = "Dashboard error: " + err.message;
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
    setInterval(loadDashboard, 5000);
  </script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Agent Memory Orchestrator daemon")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
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
