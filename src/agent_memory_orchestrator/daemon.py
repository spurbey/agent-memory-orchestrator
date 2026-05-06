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
        if path == "/graph":
            self._write_html(200, GRAPH_HTML)
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
                if path == "/api/graph":
                    include_historical = (query.get("include_historical") or ["false"])[0].lower() == "true"
                    graph_query = (query.get("query") or query.get("q") or [""])[0] or None
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "graph": svc.graph_snapshot(
                                query=graph_query,
                                session_id=session_id,
                                limit=limit,
                                include_historical=include_historical,
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
    svg { width: 100%; height: 100%; display: block; }
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
    .node { cursor: pointer; }
    .node circle { stroke: #0d1117; stroke-width: 2; }
    .node text { fill: var(--ink); font-size: 11px; paint-order: stroke; stroke: #0d1117; stroke-width: 4px; stroke-linejoin: round; }
    .edge { stroke: var(--edge); stroke-width: 1.4; opacity: 0.72; }
    .edge-label { fill: var(--muted); font-size: 10px; paint-order: stroke; stroke: #0d1117; stroke-width: 3px; }
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
      <input id="limit" type="number" value="100" min="10" max="500" style="min-width:90px;width:90px" />
      <label><input id="historical" type="checkbox" /> history</label>
      <button onclick="loadGraph()">Load Graph</button>
      <button onclick="location.href='/'">Dashboard</button>
    </div>
  </header>
  <main>
    <section id="graphWrap" class="panel">
      <svg id="graph" role="img" aria-label="Knowledge graph visualization"></svg>
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
    const svg = document.getElementById("graph");
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const short = (value, n = 90) => {
      const text = String(value ?? "").replace(/\s+/g, " ");
      return text.length > n ? text.slice(0, n - 3) + "..." : text;
    };
    const colorFor = type => ({
      file: "var(--file)",
      memory: "var(--memory)",
      memory_type: "var(--type)",
      topic: "var(--entity)"
    })[type] || "var(--entity)";
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
      const limit = document.getElementById("limit").value || "100";
      if (query) params.set("query", query);
      if (session) params.set("session_id", session);
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
      const width = svg.clientWidth || 900;
      const height = svg.clientHeight || 640;
      const cx = width / 2;
      const cy = height / 2;
      const nodeMap = new Map(nodes.map(n => [n.id, {...n}]));
      const degree = new Map(nodes.map(n => [n.id, 0]));
      for (const edge of edges) {
        degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
        degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
      }
      const ordered = [...nodeMap.values()].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0));
      const rings = [0, 150, 260, 360, 460];
      ordered.forEach((node, index) => {
        if (index === 0) {
          node.x = cx;
          node.y = cy;
        } else {
          const ring = Math.min(rings.length - 1, Math.floor(Math.sqrt(index / 4)) + 1);
          const itemsBefore = ring === 1 ? 1 : 1 + (ring - 1) * (ring - 1) * 4;
          const slot = index - itemsBefore;
          const slots = Math.max(8, ring * 10);
          const angle = (slot / slots) * Math.PI * 2 + ring * 0.31;
          node.x = cx + Math.cos(angle) * rings[ring];
          node.y = cy + Math.sin(angle) * rings[ring] * 0.72;
        }
        nodeMap.set(node.id, node);
      });

      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.innerHTML = `
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L7,3 z" fill="var(--edge)"></path>
          </marker>
        </defs>
        <g class="edges">
          ${edges.map(edge => {
            const source = nodeMap.get(edge.source);
            const target = nodeMap.get(edge.target);
            if (!source || !target) return "";
            const mx = (source.x + target.x) / 2;
            const my = (source.y + target.y) / 2;
            return `
              <line class="edge" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" marker-end="url(#arrow)"></line>
              <text class="edge-label" x="${mx}" y="${my}">${esc(edge.relation)}</text>
            `;
          }).join("")}
        </g>
        <g class="nodes">
          ${ordered.map(node => `
            <g class="node" transform="translate(${node.x},${node.y})" data-node="${esc(node.id)}">
              <circle r="${radiusFor(node) + Math.min(10, (degree.get(node.id) || 0) * 0.8)}" fill="${colorFor(node.type)}"></circle>
              <text x="${radiusFor(node) + 9}" y="4">${esc(short(node.label, 34))}</text>
            </g>
          `).join("")}
        </g>
      `;
      for (const el of svg.querySelectorAll(".node")) {
        el.addEventListener("click", () => {
          const node = nodeMap.get(el.getAttribute("data-node"));
          if (node) selectNode(node, edges.filter(e => e.source === node.id || e.target === node.id));
        });
      }
      if (!nodes.length) {
        document.getElementById("selected").innerHTML = `<div class="muted">No graph rows matched this filter.</div>`;
      }
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

    loadGraph();
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
