
const AMO = {
  view: "dashboard",
  sessions: [],
  selectedSessionId: "",
  selectedSession: null,
  health: null,
  jobs: { jobs: [], reset_marker: null },
  connectors: { slack: null },
  centralGraph: { nodes: [], edges: [], warnings: [], full: false, limit: 360 },
  versionFlow: { flows: [], nodes: [], edges: [], warnings: [] },
  graph: {
    canvas: null,
    ctx: null,
    nodes: [],
    edges: [],
    positions: new Map(),
    velocities: new Map(),
    screenPositions: new Map(),
    selectedId: "",
    hoveredId: "",
    scale: 1,
    tx: 0,
    ty: 0,
    rotationX: -0.42,
    rotationY: 0.68,
    cameraDistance: 960,
    fov: 720,
    dragging: false,
    dragStart: null,
    dragMode: "",
    spaceDown: false,
  },
};

const ANSWER_KINDS = new Set([
  "Decision", "WorkChange", "Fix", "Bug", "Blocker", "TestRun", "ContextSnapshot", "GitCommit", "Topic", "Cluster",
  "ReasoningNode", "Problem", "Cause", "Constraint", "OpenQuestion", "Commit", "Packet", "CodeNode", "CodeVersion", "Symbol",
]);
const SUPPORT_KINDS = new Set(["RawEvidenceRef", "CleanedEvidenceWindow", "GraphDelta", "Session", "Repo", "Branch", "File", "App"]);
const VERSION_EDGES = new Set([
  "COMMITTED_AS", "REFINES", "SUPERSEDES", "DUPLICATE_OF", "CONTRADICTS", "VALIDATED_BY", "MERGED_INTO",
  "REASON_NODE_EXPLAINS_COMMIT", "REASON_NODE_IN_PACKET", "COMMIT_PRODUCED_HUNK",
]);
const PIPELINE = [
  ["Raw", "Captured hook events", "raw"],
  ["Queue", "Closed sessions enqueued", "queue"],
  ["Reason", "Packet-wise Qwen + review", "reason"],
  ["Graph", "V2 Kuzu graph writes", "graph"],
  ["Index", "V2 retrieval documents", "index"],
  ["Vector", "Embeddings / FAISS", "vector"],
  ["Trace", "Answer provenance", "trace"],
];

function $(id) { return document.getElementById(id); }
function qsa(sel) { return [...document.querySelectorAll(sel)]; }
function text(value, fallback = "") { return String(value ?? fallback); }
function truncate(value, len = 96) {
  const raw = text(value).replace(/\s+/g, " ").trim();
  return raw.length > len ? raw.slice(0, len - 1) + "..." : raw;
}
function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function formatJson(value) { return JSON.stringify(value ?? {}, null, 2); }
function timeAgo(iso) {
  if (!iso) return "unknown";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const diff = Math.max(0, Date.now() - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return new Date(iso).toLocaleString();
}
function hashCode(str) {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i += 1) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}
function nodeId(node) { return text(node.id || node.node_id); }
function edgeSource(edge) { return text(edge.source_id || edge.source || edge.from || edge.sourceId); }
function edgeTarget(edge) { return text(edge.target_id || edge.target || edge.to || edge.targetId); }
function edgeKind(edge) { return text(edge.kind || edge.relation || edge.label || "RELATED"); }
function nodeLabel(node) { return text(node.label || node.summary || nodeId(node)); }
function nodeKind(node) { return text(node.kind || node.type || "Node"); }
function nodeStatus(node) { return text(node.status || "draft"); }
function nodeSummary(node) { return text(node.summary || node.label || ""); }
function metadata(node) { return node && typeof node.metadata === "object" && node.metadata ? node.metadata : {}; }
function readableKind(kind) { return kind.replace(/([a-z])([A-Z])/g, "$1 $2"); }
function isAnswerNode(node) {
  const kind = nodeKind(node);
  if (SUPPORT_KINDS.has(kind)) return false;
  const status = nodeStatus(node);
  return ANSWER_KINDS.has(kind) || node.scope === "central" || ["committed", "active", "session_final", "accepted"].includes(status);
}
function nodeColor(kind, status) {
  const color = {
    Decision: "#80dec6", WorkChange: "#f2cf78", Fix: "#b7f56e", Bug: "#ff766f", Blocker: "#ff766f",
    TestRun: "#8ab5ff", ContextSnapshot: "#d5f7df", GitCommit: "#ffffff", Topic: "#bda2ff", Cluster: "#bda2ff",
    File: "#91a69b", Repo: "#91cf7b", Branch: "#8ab5ff", RawEvidenceRef: "#67786f", CleanedEvidenceWindow: "#a5d7c4", GraphDelta: "#80dec6",
  }[kind] || "#9fb5aa";
  return status === "superseded" ? "#5f6b65" : color;
}
async function apiGet(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}
async function apiPost(path, payload) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(payload || {}) });
  const raw = await response.text();
  let parsed = {};
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = { error: raw };
    }
  }
  if (!response.ok) throw new Error(parsed.error || `${response.status} ${response.statusText}`);
  return parsed;
}
function empty(message) { return `<div class="empty-state"><div><h2>No data</h2><p>${escapeHtml(message)}</p></div></div>`; }
function setDaemon(ok, label) {
  const dot = $("daemonDot");
  dot.classList.toggle("good", !!ok);
  dot.classList.toggle("bad", !ok);
  $("daemonText").textContent = label;
}
async function loadHealth() {
  try {
    AMO.health = await apiGet("/health");
    setDaemon(true, `daemon on ${AMO.health.graph_backend || "graph"}`);
    renderHealth();
  } catch (error) {
    setDaemon(false, "daemon unavailable");
    $("healthPanel").innerHTML = `<div class="health-item"><strong>error</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}
async function loadSessions() {
  const data = await apiGet("/api/graph/sessions?limit=60");
  AMO.sessions = data.sessions || [];
  renderSessions();
  renderDashboard();
  if (!AMO.selectedSessionId && AMO.sessions[0]) await selectSession(AMO.sessions[0].session_id, { silent: true });
}
async function loadCentralGraph(options = {}) {
  const full = typeof options.full === "boolean" ? options.full : !!AMO.centralGraph.full;
  const limit = full ? 5000 : 360;
  const params = new URLSearchParams({ limit: String(limit) });
  if (full) params.set("full", "true");
  const data = await apiGet(`/api/graph/central?${params.toString()}`);
  AMO.centralGraph = {
    nodes: data.nodes || [],
    edges: data.edges || [],
    warnings: data.warnings || [],
    status: data.status || {},
    full: !!data.full,
    limit: data.limit || limit,
  };
  setupGraphFilters();
  buildGraph();
  renderGraphLoadMode();
  renderDashboard();
}
async function loadVersionFlow() {
  const commit = $("versionCommitFilter")?.value.trim() || "";
  const sessionId = $("versionSessionFilter")?.value.trim() || "";
  const params = new URLSearchParams({ limit: "120" });
  if (commit) params.set("commit", commit);
  if (sessionId) params.set("session_id", sessionId);
  const data = await apiGet(`/api/graph/version-flow?${params.toString()}`);
  AMO.versionFlow = { flows: data.flows || [], nodes: data.nodes || [], edges: data.edges || [], warnings: data.warnings || [] };
  renderVersionFlow();
}
async function loadConnectorStatus() {
  try {
    const data = await apiGet("/api/connectors/slack/status");
    AMO.connectors.slack = data;
  } catch (error) {
    AMO.connectors.slack = { ok: false, error: error.message };
  }
  renderConnectorStatus();
}
async function loadJobs() {
  try {
    AMO.jobs = await apiGet("/api/jobs?limit=50");
  } catch (error) {
    AMO.jobs = { ok: false, error: error.message, jobs: [], reset_marker: null };
  }
  renderJobs();
}
async function refreshAll() {
  await Promise.allSettled([loadHealth(), loadSessions(), loadCentralGraph(), loadVersionFlow(), loadConnectorStatus(), loadJobs()]);
  if (AMO.selectedSessionId) await selectSession(AMO.selectedSessionId, { silent: true });
}
function setView(view) {
  AMO.view = view;
  document.body.classList.toggle("graph-mode", view === "graph");
  qsa(".view").forEach(el => el.classList.toggle("active", el.id === `${view}View`));
  qsa(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  const copy = {
    dashboard: ["Dashboard", "Watch sessions move from raw capture to durable central graph memory."],
    sessions: ["Sessions", "Inspect exactly what was captured, cleaned, extracted, and promoted."],
    versions: ["Version Flow", "Inspect how session work became durable Git-backed graph memory."],
    graph: ["Knowledge Graph", "Explore committed memory first, then reveal provenance when needed."],
    retrieval: ["Retrieval", "Inspect explicit GraphRAG query planning, candidates, timing, and Qwen fallback."],
    connectors: ["Connectors", "Check local connector readiness and how external messages enter GraphRAG."],
    admin: ["Admin", "Run local daemon graph jobs and inspect diagnostics."],
  }[view] || ["AMO", "Local GraphRAG control room"];
  $("pageTitle").textContent = copy[0];
  $("pageSubtitle").textContent = copy[1];
  if (view === "graph") requestAnimationFrame(resizeGraph);
  if (view === "versions") renderVersionFlow();
  if (view === "connectors") renderConnectorStatus();
}
function renderDashboard() {
  const sessions = AMO.sessions || [];
  const nodes = AMO.centralGraph.nodes || [];
  const edges = AMO.centralGraph.edges || [];
  const committed = nodes.filter(n => nodeStatus(n) === "committed").length;
  const draft = nodes.filter(n => nodeStatus(n) === "draft").length;
  const sessionFinal = nodes.filter(n => ["session_final", "accepted"].includes(nodeStatus(n))).length;
  const rawEvents = sessions.reduce((sum, row) => sum + Number(row.raw_events || 0), 0);
  const graphCaption = sessionFinal ? `${sessionFinal} session-final, ${committed} committed` : `${committed} committed, ${draft} draft`;
  $("metricGrid").innerHTML = [
    metric("Sessions", sessions.length, "tracked workstreams"),
    metric("Raw events", rawEvents, "captured evidence records"),
    metric("Graph nodes", nodes.length, graphCaption),
    metric("Edges", edges.length, "visible central relations"),
  ].join("");
  const jobs = AMO.jobs?.jobs || [];
  renderPipeline($("pipelineStrip"), {
    raw: rawEvents,
    queue: jobs.length,
    reason: nodes.filter(n => ["ReasoningNode", "Problem", "Decision", "Cause", "Fix", "Constraint", "OpenQuestion"].includes(nodeKind(n))).length,
    graph: nodes.filter(n => metadata(n).graph_schema_version === "v2").length,
    index: nodes.filter(n => ["Packet", "Commit", "EvidenceRef", "CodeHunk", "CodeNode", "CodeVersion", "Symbol"].includes(nodeKind(n))).length,
    vector: jobs.some(j => ["embeddings", "faiss", "quality_eval"].includes(text(j.last_successful_stage || j.current_stage))) ? "ready" : "pending",
    trace: edges.filter(e => VERSION_EDGES.has(edgeKind(e))).length,
  });
  $("recentSessions").innerHTML = sessions.slice(0, 7).map(sessionCard).join("") || empty("No captured sessions yet.");
}
function metric(label, value, caption) { return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><span>${escapeHtml(caption)}</span></div>`; }
function renderHealth() {
  const h = AMO.health || {};
  const rows = [["graph backend", h.graph_backend], ["graph path", h.graph_path], ["qwen model", h.qwen_model], ["qwen timeout", `${h.qwen_timeout_seconds || "?"}s`], ["extract timeout", `${h.qwen_extract_timeout_seconds || "?"}s`], ["drain windows", h.drain_max_windows_per_run]];
  $("healthPanel").innerHTML = rows.map(([k, v]) => `<div class="health-item"><strong>${escapeHtml(v || "unknown")}</strong><span>${escapeHtml(k)}</span></div>`).join("");
}
function renderConnectorStatus() {
  const target = $("slackStatusPanel");
  if (!target) return;
  const data = AMO.connectors.slack;
  if (!data) {
    target.innerHTML = `<div class="connector-card"><span class="pill warn">checking</span><strong>Slack status loading</strong><p class="muted">Waiting for daemon status.</p></div>`;
    return;
  }
  if (!data.ok) {
    target.innerHTML = `<div class="connector-card"><span class="pill bad">error</span><strong>Slack status unavailable</strong><p class="muted">${escapeHtml(data.error || "Unknown connector error")}</p></div>`;
    return;
  }
  const slack = data.slack || {};
  const config = slack.config || {};
  const tokens = slack.tokens || {};
  const prefix = slack.prefix_check || {};
  const rows = [
    ["enabled", config.enabled ? "yes" : "no", config.enabled ? "good" : "warn"],
    ["mode", config.mode || "socket_mode", "blue"],
    ["team", config.team_id || "not set", config.team_id ? "good" : "warn"],
    ["bot user", config.bot_user_id || "not set", config.bot_user_id ? "good" : "warn"],
    ["app token", tokens.app_token ? "present" : "missing", tokens.app_token ? "good" : "bad"],
    ["bot token", tokens.bot_token ? "present" : "missing", tokens.bot_token ? "good" : "bad"],
    ["prefixes", prefix.ok ? "valid" : "check", prefix.ok ? "good" : "warn"],
    ["reply mode", "tagged answer", "good"],
  ];
  target.innerHTML = `<div class="connector-card">
    <div class="panel-head"><div><span class="pill ${config.enabled ? "good" : "warn"}">Slack</span><h3>Local Socket Mode</h3></div><span class="pill blue">mention-only</span></div>
    <div class="connector-grid">${rows.map(([label, value, tone]) => `<div class="connector-kv"><span>${escapeHtml(label)}</span><strong class="${escapeHtml(tone)}">${escapeHtml(value)}</strong></div>`).join("")}</div>
    <p class="muted">${escapeHtml(data.behavior || "Answers only when tagged.")}</p>
  </div>`;
  const command = $("slackCommandPanel");
  if (command) command.textContent = data.run_command ? `amo-cli slack setup-wizard\n${data.run_command}` : command.textContent;
}
function renderJobs() {
  const list = $("v2JobsList");
  if (!list) return;
  const marker = AMO.jobs?.reset_marker;
  const markerTarget = $("v2ResetMarker");
  if (markerTarget) {
    markerTarget.innerHTML = marker
      ? `<span class="pill good">V2 reset ${escapeHtml(marker.production_v2_reset_applied_at || marker.updated_at || "applied")}</span><span class="pill blue">${escapeHtml(marker.pipeline_version || "v2")}</span>`
      : `<span class="pill warn">V2 production reset marker missing</span><span class="pill">run explicit backup-first reset before V2 graph writes</span>`;
  }
  if (!AMO.jobs?.ok) {
    list.innerHTML = `<pre class="code-block">${escapeHtml(AMO.jobs?.error || "Unable to load jobs")}</pre>`;
    return;
  }
  const jobs = AMO.jobs.jobs || [];
  list.innerHTML = jobs.map(renderJobCard).join("") || empty("No V2 session jobs yet.");
}
function renderJobCard(job) {
  const status = text(job.status || "unknown");
  const cls = status === "complete" ? "good" : status === "failed" ? "bad" : status === "pending_model" ? "warn" : "blue";
  const retry = ["failed", "pending_model"].includes(status)
    ? `<button class="btn ghost retry-job-btn" data-job-id="${escapeHtml(job.job_id)}">Retry</button>`
    : "";
  const error = job.error && Object.keys(job.error).length ? `<pre class="code-block small">${escapeHtml(formatJson(job.error))}</pre>` : "";
  return `<article class="job-card">
    <div class="panel-head">
      <div>
        <p class="eyebrow">${escapeHtml(job.source_app || "session")}</p>
        <h3>${escapeHtml(job.session_id || job.job_id)}</h3>
      </div>
      <div class="button-row"><span class="pill ${cls}">${escapeHtml(status)}</span>${retry}</div>
    </div>
    <div class="job-meta">
      <span>stage ${escapeHtml(job.current_stage || "-")}</span>
      <span>last ${escapeHtml(job.last_successful_stage || "-")}</span>
      <span>attempts ${escapeHtml(job.attempt_count || 0)}</span>
      <span>${escapeHtml(truncate(job.repo_path || job.artifact_dir || "", 92))}</span>
    </div>
    ${error}
  </article>`;
}
async function retryJob(jobId) {
  const output = $("adminOutput");
  if (output) output.textContent = `Retrying ${jobId}...`;
  try {
    const result = await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { forced_by: "dashboard" });
    if (output) output.textContent = formatJson(result);
    await loadJobs();
  } catch (error) {
    if (output) output.textContent = error.stack || error.message;
  }
}
function renderPipeline(target, counts) {
  target.innerHTML = PIPELINE.map(([name, desc, key]) => `<div class="stage-card"><strong>${escapeHtml(name)}</strong><div class="count">${escapeHtml(counts[key] ?? 0)}</div><small>${escapeHtml(desc)}</small></div>`).join("");
}
function renderSessions() {
  const html = (AMO.sessions || []).map(sessionCard).join("") || empty("No captured sessions yet.");
  $("sessionList").innerHTML = html;
  $("recentSessions").innerHTML = (AMO.sessions || []).slice(0, 7).map(sessionCard).join("") || empty("No captured sessions yet.");
}
function sessionCard(row) {
  const id = text(row.session_id);
  const selected = id === AMO.selectedSessionId ? " active" : "";
  const counts = row.graph_counts || {};
  const sources = (row.source_apps || []).join(", ") || "unknown";
  return `<article class="session-card${selected}" data-session-id="${escapeHtml(id)}"><div class="id">${escapeHtml(id)}</div><div class="muted small">${escapeHtml(truncate(row.cwd || row.repo || "local session", 72))}</div><div class="session-meta"><span class="pill good">${Number(row.raw_events || 0)} raw</span><span class="pill blue">${escapeHtml(row.latest_event || "event")}</span><span class="pill">${escapeHtml(sources)}</span><span class="pill warn">${Number(counts.draft || 0)} draft</span><span class="pill good">${Number(counts.committed || 0)} committed</span><span class="pill">${escapeHtml(timeAgo(row.latest_at))}</span></div></article>`;
}
async function selectSession(sessionId, options = {}) {
  AMO.selectedSessionId = sessionId;
  renderSessions();
  try {
    const data = await apiGet(`/api/graph/session-detail?session_id=${encodeURIComponent(sessionId)}&limit=220`);
    AMO.selectedSession = data;
    renderSessionDetail(data);
    if (!options.silent) setView("sessions");
  } catch (error) {
    AMO.selectedSession = null;
    $("sessionContent").classList.add("hidden");
    $("sessionEmpty").classList.remove("hidden");
    $("sessionEmpty").innerHTML = `<h2>Session failed to load</h2><p>${escapeHtml(error.message)}</p>`;
  }
}
function renderSessionDetail(data) {
  $("sessionEmpty").classList.add("hidden");
  $("sessionContent").classList.remove("hidden");
  $("sessionTitle").textContent = data.session_id;
  const timeline = data.timeline || [];
  const nodes = data.graph?.nodes || [];
  const edges = data.graph?.edges || [];
  const contextNode = data.current_context?.nodes?.[0];
  $("sessionSource").textContent = `Session ${timeline[0]?.source_app || "local"}`;
  $("sessionSummary").textContent = contextNode?.summary || `${timeline.length} captured events, ${nodes.length} graph nodes, ${edges.length} graph edges.`;
  const windows = data.windows || [];
  const preview = data.merge_preview || {};
  renderPipeline($("sessionPipeline"), {
    raw: timeline.length,
    queue: AMO.jobs?.jobs?.filter(j => j.session_id === data.session_id).length || 0,
    reason: nodes.filter(n => ["ReasoningNode", "Problem", "Decision", "Cause", "Fix", "Constraint", "OpenQuestion"].includes(nodeKind(n))).length,
    graph: nodes.filter(n => metadata(n).graph_schema_version === "v2").length,
    index: nodes.filter(n => ["Packet", "Commit", "EvidenceRef", "CodeHunk", "CodeNode", "CodeVersion", "Symbol"].includes(nodeKind(n))).length,
    vector: data.pending?.count ? "pending" : "ready",
    trace: edges.filter(e => VERSION_EDGES.has(edgeKind(e))).length,
  });
  $("timelineList").innerHTML = timeline.map(renderTimeline).join("") || empty("No raw evidence for this session.");
  $("cleanedWindows").innerHTML = windows.map(renderWindow).join("") || empty("No evidence view artifacts yet. A closed-session V2 job must run first.");
  $("draftNodes").innerHTML = nodes.map(renderNodeCard).join("") || empty("No session graph nodes yet.");
  $("mergePreview").innerHTML = renderMergePreview(preview);
}
function renderTimeline(row) {
  return `<article class="timeline-item"><div class="panel-head"><strong>${escapeHtml(row.event_name || "event")}</strong><span class="pill">${escapeHtml(timeAgo(row.created_at))}</span></div><div class="muted small">evidence ${escapeHtml(row.evidence_id || row.id || "")}</div><pre class="code-block">${escapeHtml(formatJson(row.payload || row))}</pre></article>`;
}
function renderWindow(row, index) {
  const trigger = row.trigger || {};
  return `<article class="window-card"><div class="panel-head"><strong>Window ${index + 1}</strong><span class="pill good">${escapeHtml(trigger.trigger_type || "trigger")}</span></div><p class="muted">${escapeHtml(trigger.reason || "Cleaned bounded evidence prepared for graph extraction.")}</p><pre class="code-block">${escapeHtml(formatJson(row.cleaned_evidence || row))}</pre></article>`;
}
function renderNodeCard(node) {
  const kind = nodeKind(node);
  return `<article class="node-card" data-node-id="${escapeHtml(nodeId(node))}"><div class="panel-head"><strong>${escapeHtml(readableKind(kind))}</strong><span class="pill ${nodeStatus(node) === "committed" ? "good" : "warn"}">${escapeHtml(nodeStatus(node))}</span></div><p>${escapeHtml(truncate(nodeSummary(node), 180))}</p><div class="muted small">${escapeHtml(nodeId(node))}</div></article>`;
}
function renderMergePreview(preview) {
  if (!preview || preview.ok === false) return empty(preview?.error || "No merge preview available.");
  const promotions = preview.promotions || preview.promoted_nodes || preview.planned_promotions || [];
  const edges = preview.version_edges || preview.edges || preview.relations || [];
  const rows = [];
  rows.push(`<div class="merge-card"><strong>${escapeHtml(preview.apply ? "Applied merge" : "Dry run merge plan")}</strong><p class="muted">${promotions.length} promotions, ${edges.length} version edges, ${Number(preview.review_candidates?.length || 0)} review candidates.</p></div>`);
  promotions.slice(0, 12).forEach(item => rows.push(`<div class="merge-card"><span class="pill good">promote</span><p>${escapeHtml(truncate(item.summary || item.label || item.node_id || item.id, 180))}</p></div>`));
  edges.slice(0, 12).forEach(edge => rows.push(`<div class="merge-card"><span class="pill blue">${escapeHtml(edge.kind || edge.relation || "edge")}</span><p class="muted small">${escapeHtml(edge.source_id || edge.source || "")} -> ${escapeHtml(edge.target_id || edge.target || "")}</p></div>`));
  return rows.join("") || empty("No answer-grade draft nodes are ready to promote.");
}
function renderVersionFlow() {
  const flows = AMO.versionFlow?.flows || [];
  const warnings = AMO.versionFlow?.warnings || [];
  const warningTarget = $("versionWarnings");
  const listTarget = $("versionFlowList");
  if (!warningTarget || !listTarget) return;
  warningTarget.innerHTML = warnings.map(w => `<div class="warning-card">${escapeHtml(w)}</div>`).join("");
  listTarget.innerHTML = flows.map(renderVersionFlowCard).join("") || empty("No committed version flows found yet. Drain a write session, finalize it to a commit, then reload this page.");
}
function renderVersionFlowCard(flow) {
  const commit = flow.commit_node || {};
  const counts = flow.counts || {};
  const work = flow.work_nodes || [];
  const files = flow.files || [];
  const tests = flow.tests || [];
  const versionEdges = flow.version_edges || [];
  const evidence = flow.evidence_ids || [];
  return `<article class="version-card" data-commit-id="${escapeHtml(flow.commit_id || commit.commit_id || "")}">
    <div class="version-card-head">
      <div>
        <p class="eyebrow">Commit</p>
        <h2>${escapeHtml(commit.label || truncate(flow.commit_id || "commit", 16))}</h2>
        <p class="muted">${escapeHtml(truncate(commit.summary || flow.summary || "", 220))}</p>
      </div>
      <div class="version-counts">
        <span class="pill good">${Number(counts.work_nodes || 0)} promoted</span>
        <span class="pill blue">${Number(counts.files || 0)} files</span>
        <span class="pill warn">${Number(counts.version_edges || 0)} version edges</span>
        <span class="pill">${Number(counts.evidence_refs || 0)} evidence refs</span>
      </div>
    </div>
    <div class="version-lanes">
      ${versionLane("Promoted Memory", work.map(renderVersionNode).join("") || "<p class='muted'>No promoted answer nodes visible.</p>")}
      ${versionLane("Modified Files", files.map(renderVersionNode).join("") || "<p class='muted'>No modified files linked.</p>")}
      ${versionLane("Validation", tests.map(renderVersionNode).join("") || "<p class='muted'>No test nodes linked.</p>")}
      ${versionLane("Version Edges", versionEdges.map(renderVersionEdge).join("") || "<p class='muted'>No refine/supersede/duplicate edges yet.</p>")}
      ${versionLane("Evidence Refs", evidence.map(id => `<div class="evidence-chip">${escapeHtml(id)}</div>`).join("") || "<p class='muted'>No evidence refs linked.</p>")}
    </div>
  </article>`;
}
function versionLane(title, body) {
  return `<section class="version-lane"><h3>${escapeHtml(title)}</h3>${body}</section>`;
}
function renderVersionNode(node) {
  return `<div class="version-node" data-node-id="${escapeHtml(nodeId(node))}">
    <span class="pill ${nodeStatus(node) === "committed" ? "good" : "warn"}">${escapeHtml(nodeKind(node))}</span>
    <strong>${escapeHtml(truncate(nodeLabel(node), 84))}</strong>
    <p>${escapeHtml(truncate(nodeSummary(node), 160))}</p>
  </div>`;
}
function renderVersionEdge(edge) {
  return `<div class="version-edge">
    <span class="pill blue">${escapeHtml(edgeKind(edge))}</span>
    <p class="muted small">${escapeHtml(edgeSource(edge))} -> ${escapeHtml(edgeTarget(edge))}</p>
  </div>`;
}
async function drainSelected() {
  if (!AMO.selectedSessionId) return;
  const result = await apiPost("/graph/drain", { session_id: AMO.selectedSessionId, limit: 100, max_windows: 5 });
  await selectSession(AMO.selectedSessionId, { silent: true });
  await loadCentralGraph();
  $("mergePreview").insertAdjacentHTML("afterbegin", `<div class="merge-card"><span class="pill good">drain</span><pre class="code-block">${escapeHtml(formatJson(result))}</pre></div>`);
}
async function previewFinalize() {
  if (!AMO.selectedSessionId) return;
  const result = await apiPost("/graph/finalize-session", { session_id: AMO.selectedSessionId, commit: "HEAD", apply: false, limit: 500 });
  $("mergePreview").innerHTML = renderMergePreview(result);
}
async function runRetrieval() {
  const query = $("retrievalQuery").value.trim() || $("globalSearch").value.trim();
  if (!query) return;
  $("retrievalResult").innerHTML = `<section class="panel"><p class="muted">Searching graph memory...</p></section>`;
  setView("retrieval");
  try {
    const result = await apiPost("/graph/retrieve", {
      query,
      limit: 10,
      use_vector: true,
      require_vector: $("requireVector")?.checked ?? false,
      include_answer: true,
    });
    renderRetrievalResult(result);
  } catch (error) {
    $("retrievalResult").innerHTML = `<section class="panel"><h2>Search failed</h2><p class="muted">${escapeHtml(error.message)}</p></section>`;
  }
}
function renderRetrievalResult(result) {
  if (result?.ok === false) {
    $("retrievalResult").innerHTML = `<section class="panel"><h2>V2 retrieval is not ready</h2><p class="muted">${escapeHtml(result.error || "Unknown retrieval error")}</p>${result.hint ? `<p class="muted">${escapeHtml(result.hint)}</p>` : ""}<pre class="code-block">${escapeHtml(formatJson({ graph_path: result.graph_path, db_path: result.db_path }))}</pre></section>`;
    return;
  }
  renderIndexedRetrievalResult(result);
}
function renderIndexedRetrievalResult(result) {
  const retrieval = result.retrieval || {};
  const answer = result.answer || {};
  const hits = retrieval.hits || [];
  const citations = answer.citations || [];
  const candidateCounts = retrieval.candidate_counts || {};
  const source = `${result.graph_scope || "default scope"} | ${truncate(result.db_path || "", 80)}`;
  $("retrievalResult").innerHTML = `
    <section class="panel">
      <div class="result-grid">
        <div>
          <p class="eyebrow">Indexed V2 retrieval</p>
          <h2>${escapeHtml(retrieval.intent || "general")}</h2>
          <p class="muted">${escapeHtml(source)}</p>
          <div class="session-meta">
            <span class="pill ${String(retrieval.vector_status || "").includes("completed") ? "good" : "warn"}">vector ${escapeHtml(retrieval.vector_status || "unknown")}</span>
            <span class="pill blue">${escapeHtml(retrieval.reranker || "deterministic")}</span>
            <span class="pill good">${escapeHtml(hits.length)} hits</span>
          </div>
          <div class="retrieval-counts">${Object.entries(candidateCounts).map(([key, value]) => `<span>${escapeHtml(key)} ${escapeHtml(value)}</span>`).join("")}</div>
        </div>
        <pre class="code-block">${escapeHtml(answer.text || "No generated answer returned.")}</pre>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Answer citations</h2><span class="pill good">${citations.length}</span></div>
      <div class="retrieval-citations">${citations.map(renderCitationCard).join("") || empty("No citations returned.")}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Ranked hits</h2><span class="pill good">${hits.length}</span></div>
      <div class="retrieval-hits">${hits.map(renderRetrievalHitCard).join("") || empty("No indexed retrieval hits matched.")}</div>
    </section>`;
}
function renderCitationCard(citation) {
  const trace = citation.trace || {};
  const chain = Array.isArray(trace.chain) ? trace.chain : [];
  return `<article class="retrieval-card">
    <div class="panel-head"><div><p class="eyebrow">rank ${escapeHtml(citation.rank || "")}</p><h3>${escapeHtml(citation.packet_id || citation.graph_node_id || citation.doc_id || "citation")}</h3></div><span class="pill blue">${escapeHtml(citation.doc_type || "doc")}</span></div>
    <p class="muted">commit ${escapeHtml((citation.commit_shas || [citation.commit_sha]).filter(Boolean).join(", ") || "-")} | evidence ${escapeHtml((citation.evidence_ids || []).slice(0, 4).join(", ") || "-")}</p>
    <p class="muted">code ${escapeHtml((citation.code_nodes || citation.code_node_ids || []).slice(0, 3).join(", ") || "-")}</p>
    ${chain.length ? `<pre class="code-block small">${escapeHtml(chain.map(item => `${item.role || item.kind || "node"}: ${item.label || item.summary || item.id}`).join("\n"))}</pre>` : ""}
  </article>`;
}
function renderRetrievalHitCard(hit, index) {
  const doc = hit.document || {};
  const node = hit.graph_node || {};
  const title = doc.title || node.label || doc.graph_node_id || `hit ${index + 1}`;
  return `<article class="retrieval-card">
    <div class="panel-head"><div><p class="eyebrow">${escapeHtml(doc.doc_type || node.kind || "hit")}</p><h3>${escapeHtml(title)}</h3></div><span class="pill good">${escapeHtml(hit.score ?? "")}</span></div>
    <p class="muted">packet ${escapeHtml(doc.packet_id || "-")} | commit ${escapeHtml(doc.commit_sha || "-")} | node ${escapeHtml(doc.graph_node_id || node.id || "-")}</p>
    <p>${escapeHtml(truncate(doc.body || node.summary || "", 360))}</p>
    <p class="muted">${escapeHtml((hit.sources || []).join(", ") || "no source labels")} | ${escapeHtml((hit.reasons || []).slice(0, 6).join(" | "))}</p>
  </article>`;
}
async function runAdminJob(kind) {
  const output = $("adminOutput");
  output.textContent = "Running...";
  try {
    let result;
    if (kind === "consolidate") result = await apiPost("/graph/consolidate", { limit: 500, apply: false });
    if (kind === "cache") result = await apiPost("/graph/rebuild-cache", { limit: 5000 });
    if (kind === "debugGraph") result = await apiGet("/api/debug/graph?limit=50");
    if (kind === "debugQwen") result = await apiGet("/api/debug/qwen?sample=classify%20latest%20graph%20work");
    output.textContent = formatJson(result);
  } catch (error) {
    output.textContent = error.stack || error.message;
  }
}
function setupGraphFilters() {
  const nodes = AMO.centralGraph.nodes || [];
  const kinds = [...new Set(nodes.map(nodeKind))].sort();
  const statuses = [...new Set(nodes.map(nodeStatus))].sort();
  $("kindFilter").innerHTML = `<option value="">All kinds</option>` + kinds.map(k => `<option>${escapeHtml(k)}</option>`).join("");
  $("statusFilter").innerHTML = `<option value="">All status</option>` + statuses.map(s => `<option>${escapeHtml(s)}</option>`).join("");
}
function renderGraphLoadMode() {
  $("graphSliceBtn")?.classList.toggle("primary", !AMO.centralGraph.full);
  $("graphFullBtn")?.classList.toggle("primary", !!AMO.centralGraph.full);
}
function buildGraph() {
  const query = $("graphFilter")?.value.trim().toLowerCase() || $("globalSearch")?.value.trim().toLowerCase() || "";
  const kindFilter = $("kindFilter")?.value || "";
  const statusFilter = $("statusFilter")?.value || "";
  const showSupport = $("supportToggle")?.checked || false;
  const nodes = (AMO.centralGraph.nodes || []).filter(node => {
    if (!showSupport && !isAnswerNode(node)) return false;
    if (kindFilter && nodeKind(node) !== kindFilter) return false;
    if (statusFilter && nodeStatus(node) !== statusFilter) return false;
    if (query) {
      const hay = `${nodeKind(node)} ${nodeLabel(node)} ${nodeSummary(node)} ${nodeId(node)} ${formatJson(metadata(node))}`.toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });
  const ids = new Set(nodes.map(nodeId));
  const edges = (AMO.centralGraph.edges || []).filter(edge => ids.has(edgeSource(edge)) && ids.has(edgeTarget(edge)));
  AMO.graph.nodes = nodes;
  AMO.graph.edges = edges;
  seedPositions(nodes);
  simulateGraph(nodes.length > 1000 ? 8 : 80);
  drawGraph();
  renderGraphMini();
}
function seedPositions(nodes) {
  const g = AMO.graph;
  const largeGraph = nodes.length > 1000;
  const count = Math.max(1, nodes.length);
  nodes.forEach((node, index) => {
    const id = nodeId(node);
    const h = hashCode(id);
    if (!g.positions.has(id)) {
      const theta = ((h % 7200) / 7200) * Math.PI * 2;
      const phi = Math.acos(1 - (2 * (index + 0.5)) / count);
      const shell = largeGraph
        ? 260 + Math.cbrt(index + 1) * 44 + ((h >>> 8) % 180)
        : 180 + (index % 7) * 42 + ((h >>> 8) % 95);
      g.positions.set(id, {
        x: Math.sin(phi) * Math.cos(theta) * shell,
        y: Math.sin(phi) * Math.sin(theta) * shell,
        z: Math.cos(phi) * shell + ((h >>> 16) % 180) - 90,
      });
      g.velocities.set(id, { x: 0, y: 0, z: 0 });
    } else {
      const pos = g.positions.get(id);
      if (pos && typeof pos.z !== "number") pos.z = ((h >>> 16) % 240) - 120;
      const velocity = g.velocities.get(id);
      if (velocity && typeof velocity.z !== "number") velocity.z = 0;
    }
  });
}
function simulateGraph(iterations = 1) {
  const g = AMO.graph;
  const nodes = g.nodes;
  const edges = g.edges;
  const idToNode = new Map(nodes.map(n => [nodeId(n), n]));
  const pairwiseRepulsion = nodes.length <= 900;
  for (let step = 0; step < iterations; step += 1) {
    if (pairwiseRepulsion) {
      for (let i = 0; i < nodes.length; i += 1) {
        const a = nodes[i];
        const pa = g.positions.get(nodeId(a));
        const va = g.velocities.get(nodeId(a));
        if (!pa || !va) continue;
        for (let j = i + 1; j < nodes.length; j += 1) {
          const b = nodes[j];
          const pb = g.positions.get(nodeId(b));
          const vb = g.velocities.get(nodeId(b));
          if (!pb || !vb) continue;
          let dx = pa.x - pb.x;
          let dy = pa.y - pb.y;
          let dz = (pa.z || 0) - (pb.z || 0);
          const dist2 = dx * dx + dy * dy + dz * dz + 160;
          const force = Math.min(2.7, 1600 / dist2);
          const dist = Math.sqrt(dist2);
          dx /= dist;
          dy /= dist;
          dz /= dist;
          va.x += dx * force;
          va.y += dy * force;
          va.z += dz * force;
          vb.x -= dx * force;
          vb.y -= dy * force;
          vb.z -= dz * force;
        }
      }
    }
    edges.forEach(edge => {
      const s = edgeSource(edge);
      const t = edgeTarget(edge);
      if (!idToNode.has(s) || !idToNode.has(t)) return;
      const ps = g.positions.get(s);
      const pt = g.positions.get(t);
      const vs = g.velocities.get(s);
      const vt = g.velocities.get(t);
      if (!ps || !pt || !vs || !vt) return;
      const dx = pt.x - ps.x;
      const dy = pt.y - ps.y;
      const dz = (pt.z || 0) - (ps.z || 0);
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      const desired = VERSION_EDGES.has(edgeKind(edge)) ? 156 : 210;
      const force = (dist - desired) * 0.01;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      const fz = (dz / dist) * force;
      vs.x += fx; vs.y += fy; vs.z += fz;
      vt.x -= fx; vt.y -= fy; vt.z -= fz;
    });
    nodes.forEach(node => {
      const id = nodeId(node);
      const p = g.positions.get(id);
      const v = g.velocities.get(id);
      if (!p || !v) return;
      v.x += -p.x * 0.0024;
      v.y += -p.y * 0.0024;
      v.z += -(p.z || 0) * 0.0024;
      v.x *= 0.84;
      v.y *= 0.84;
      v.z *= 0.84;
      p.x += Math.max(-13, Math.min(13, v.x));
      p.y += Math.max(-13, Math.min(13, v.y));
      p.z = (p.z || 0) + Math.max(-13, Math.min(13, v.z));
    });
  }
}
function setupGraphCanvas() {
  const canvas = $("graphCanvas");
  if (!canvas) return;
  AMO.graph.canvas = canvas;
  AMO.graph.ctx = canvas.getContext("2d");
  resizeGraph();
  canvas.addEventListener("wheel", onGraphWheel, { passive: false });
  canvas.addEventListener("contextmenu", event => event.preventDefault());
  canvas.addEventListener("pointerdown", onGraphPointerDown);
  canvas.addEventListener("pointermove", onGraphPointerMove);
  canvas.addEventListener("pointerup", onGraphPointerUp);
  canvas.addEventListener("pointerleave", onGraphPointerUp);
  canvas.addEventListener("dblclick", () => focusSelectedNeighbors());
  window.addEventListener("keydown", onGraphKeyDown);
  window.addEventListener("keyup", onGraphKeyUp);
  window.addEventListener("resize", resizeGraph);
}
function resizeGraph() {
  const canvas = AMO.graph.canvas;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  AMO.graph.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  drawGraph();
}
function rotateGraphPoint(p) {
  const g = AMO.graph;
  const x = p.x || 0;
  const y = p.y || 0;
  const z = p.z || 0;
  const cy = Math.cos(g.rotationY);
  const sy = Math.sin(g.rotationY);
  const cx = Math.cos(g.rotationX);
  const sx = Math.sin(g.rotationX);
  const x1 = x * cy - z * sy;
  const z1 = x * sy + z * cy;
  const y2 = y * cx - z1 * sx;
  const z2 = y * sx + z1 * cx;
  return { x: x1, y: y2, z: z2 };
}
function worldToScreen(p) {
  const g = AMO.graph;
  const canvas = g.canvas;
  if (!canvas) return { x: 0, y: 0, z: 0, depth: 1, perspective: 1, visible: false };
  const rect = canvas.getBoundingClientRect();
  const rotated = rotateGraphPoint(p);
  const depth = Math.max(80, g.cameraDistance - rotated.z);
  const perspective = (g.fov / depth) * g.scale;
  return {
    x: rect.width / 2 + g.tx + rotated.x * perspective,
    y: rect.height / 2 + g.ty + rotated.y * perspective,
    z: rotated.z,
    depth,
    perspective,
    visible: depth > 30,
  };
}
function graphPoint(event) { const rect = AMO.graph.canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; }
function hitNode(event) {
  const point = graphPoint(event);
  let best = null;
  let bestDist = Infinity;
  AMO.graph.nodes.forEach(node => {
    const sp = AMO.graph.screenPositions.get(nodeId(node)) || worldToScreen(AMO.graph.positions.get(nodeId(node)) || {});
    if (!sp.visible) return;
    const r = sp.radius + 9;
    const dx = point.x - sp.x;
    const dy = point.y - sp.y;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < r && d < bestDist) { best = node; bestDist = d; }
  });
  return best;
}
function onGraphWheel(event) {
  event.preventDefault();
  const g = AMO.graph;
  const factor = event.deltaY > 0 ? 0.88 : 1.13;
  g.scale = Math.max(0.12, Math.min(6.5, g.scale * factor));
  drawGraph();
}
function onGraphPointerDown(event) {
  const g = AMO.graph;
  const hit = hitNode(event);
  if (hit) selectGraphNode(nodeId(hit));
  g.dragging = true;
  g.dragMode = event.shiftKey || g.spaceDown || event.button === 1 || event.button === 2 ? "pan" : "rotate";
  g.dragStart = {
    x: event.clientX,
    y: event.clientY,
    tx: g.tx,
    ty: g.ty,
    rotationX: g.rotationX,
    rotationY: g.rotationY,
  };
  g.canvas.classList.add("dragging");
  g.canvas.setPointerCapture(event.pointerId);
}
function onGraphPointerMove(event) {
  const g = AMO.graph;
  if (g.dragging && g.dragStart) {
    const dx = event.clientX - g.dragStart.x;
    const dy = event.clientY - g.dragStart.y;
    if (g.dragMode === "pan") {
      g.tx = g.dragStart.tx + dx;
      g.ty = g.dragStart.ty + dy;
    } else {
      g.rotationY = g.dragStart.rotationY + dx * 0.006;
      g.rotationX = Math.max(-1.35, Math.min(1.35, g.dragStart.rotationX + dy * 0.005));
    }
    drawGraph();
    return;
  }
  const hit = hitNode(event);
  const id = hit ? nodeId(hit) : "";
  if (id !== g.hoveredId) { g.hoveredId = id; drawGraph(); }
}
function onGraphPointerUp(event) {
  const g = AMO.graph;
  g.dragging = false;
  g.dragStart = null;
  g.dragMode = "";
  g.canvas?.classList.remove("dragging");
  try { g.canvas?.releasePointerCapture(event.pointerId); } catch (_) {}
}
function onGraphKeyDown(event) {
  if (AMO.view !== "graph") return;
  const g = AMO.graph;
  if (event.code === "Space") {
    g.spaceDown = true;
    event.preventDefault();
    return;
  }
  const key = event.key.toLowerCase();
  const step = event.shiftKey ? 72 : 34;
  if (key === "arrowleft" || key === "a") {
    g.tx += step;
  } else if (key === "arrowright" || key === "d") {
    g.tx -= step;
  } else if (key === "arrowup" || key === "w") {
    g.ty += step;
  } else if (key === "arrowdown" || key === "s") {
    g.ty -= step;
  } else if (key === "+" || key === "=") {
    g.scale = Math.min(6.5, g.scale * 1.12);
  } else if (key === "-" || key === "_") {
    g.scale = Math.max(0.12, g.scale * 0.88);
  } else if (key === "0") {
    g.tx = 0;
    g.ty = 0;
    g.scale = 1;
    g.rotationX = -0.42;
    g.rotationY = 0.68;
  } else {
    return;
  }
  event.preventDefault();
  drawGraph();
}
function onGraphKeyUp(event) {
  if (event.code === "Space") AMO.graph.spaceDown = false;
}
function drawGraph() {
  const g = AMO.graph;
  const canvas = g.canvas;
  const ctx = g.ctx;
  if (!canvas || !ctx) return;
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.save();
  const bg = ctx.createRadialGradient(rect.width * 0.52, rect.height * 0.48, 0, rect.width * 0.52, rect.height * 0.48, Math.max(rect.width, rect.height) * 0.72);
  bg.addColorStop(0, "#0c1712");
  bg.addColorStop(0.48, "#040907");
  bg.addColorStop(1, "#020504");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, rect.width, rect.height);
  const selectedNeighbors = neighborIds(g.selectedId);
  const selectedEdges = selectedEdgeIds(g.selectedId);
  g.screenPositions = new Map();
  g.nodes.forEach(node => {
    const id = nodeId(node);
    const pos = g.positions.get(id);
    if (!pos) return;
    const projected = worldToScreen(pos);
    projected.radius = radiusForNode(node) * Math.max(0.68, Math.min(2.8, projected.perspective));
    g.screenPositions.set(id, projected);
  });
  ctx.lineCap = "round";
  const edgeDrawList = g.edges
    .map(edge => ({ edge, a: g.screenPositions.get(edgeSource(edge)), b: g.screenPositions.get(edgeTarget(edge)) }))
    .filter(item => item.a?.visible && item.b?.visible)
    .sort((left, right) => ((left.a.z + left.b.z) / 2) - ((right.a.z + right.b.z) / 2));
  edgeDrawList.forEach(({ edge, a, b }) => {
    const kind = edgeKind(edge);
    const highlighted = selectedEdges.has(text(edge.id)) || (g.selectedId && (edgeSource(edge) === g.selectedId || edgeTarget(edge) === g.selectedId));
    const version = VERSION_EDGES.has(kind);
    const depthAlpha = Math.max(0.08, Math.min(0.58, 1.04 - ((a.depth + b.depth) / 2) / 1400));
    ctx.globalAlpha = g.selectedId ? (highlighted ? 0.92 : 0.10) : (version ? depthAlpha + 0.08 : depthAlpha);
    ctx.strokeStyle = version ? "#b7f56e" : "#668b7a";
    ctx.lineWidth = highlighted ? 2.4 : Math.max(0.7, Math.min(1.7, (a.perspective + b.perspective) / 2));
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    if (highlighted && g.scale > 0.42) {
      ctx.globalAlpha = 0.86;
      ctx.fillStyle = "#9fb5aa";
      ctx.font = "11px ui-monospace, monospace";
      ctx.fillText(kind, (a.x + b.x) / 2 + 6, (a.y + b.y) / 2 - 6);
    }
  });
  const nodeDrawList = [...g.nodes]
    .map(node => ({ node, projected: g.screenPositions.get(nodeId(node)) }))
    .filter(item => item.projected?.visible)
    .sort((left, right) => left.projected.z - right.projected.z);
  nodeDrawList.forEach(({ node, projected }) => {
    const id = nodeId(node);
    const selected = id === g.selectedId;
    const neighbor = selectedNeighbors.has(id);
    const hovered = id === g.hoveredId;
    const depthAlpha = Math.max(0.32, Math.min(1, 1.08 - projected.depth / 1700));
    const alpha = g.selectedId ? (selected || neighbor ? 1 : 0.18) : depthAlpha;
    const r = projected.radius * (selected ? 1.65 : hovered ? 1.34 : 1);
    ctx.globalAlpha = alpha;
    if (selected || hovered) {
      ctx.globalAlpha = selected ? 0.34 : 0.20;
      ctx.beginPath();
      ctx.arc(projected.x, projected.y, r * 3.2, 0, Math.PI * 2);
      ctx.fillStyle = nodeColor(nodeKind(node), nodeStatus(node));
      ctx.fill();
      ctx.globalAlpha = alpha;
    }
    ctx.beginPath();
    ctx.arc(projected.x, projected.y, r, 0, Math.PI * 2);
    ctx.fillStyle = nodeColor(nodeKind(node), nodeStatus(node));
    ctx.fill();
    ctx.strokeStyle = selected ? "#ffffff" : nodeStatus(node) === "draft" ? "rgba(255,255,255,0.38)" : "rgba(4,9,7,0.9)";
    ctx.lineWidth = selected ? 3 : 1.6;
    ctx.stroke();
    const showLabels = $("labelsToggle")?.checked && (selected || hovered || neighbor || projected.perspective > 0.78 || g.nodes.length < 44);
    if (showLabels) {
      ctx.globalAlpha = selected || hovered ? 1 : Math.min(0.88, alpha + 0.18);
      ctx.font = `${selected ? 13 : 11}px ui-monospace, monospace`;
      ctx.fillStyle = selected ? "#eff7ef" : "#c8d8d0";
      ctx.fillText(`${nodeKind(node)}: ${truncate(nodeLabel(node), selected ? 46 : 28)}`, projected.x + r + 5, projected.y + 4);
    }
  });
  ctx.restore();
}
function radiusForNode(node) {
  const kind = nodeKind(node);
  if (kind === "Topic" || kind === "Cluster") return 10;
  if (kind === "GitCommit") return 7;
  if (nodeStatus(node) === "committed") return 6.5;
  if (SUPPORT_KINDS.has(kind)) return 4.4;
  return 5.8;
}
function neighborIds(id) {
  const ids = new Set();
  if (!id) return ids;
  ids.add(id);
  AMO.graph.edges.forEach(edge => { if (edgeSource(edge) === id) ids.add(edgeTarget(edge)); if (edgeTarget(edge) === id) ids.add(edgeSource(edge)); });
  return ids;
}
function selectedEdgeIds(id) {
  const ids = new Set();
  if (!id) return ids;
  AMO.graph.edges.forEach(edge => { if (edgeSource(edge) === id || edgeTarget(edge) === id) ids.add(text(edge.id)); });
  return ids;
}
function selectGraphNode(id) { AMO.graph.selectedId = id; renderNodeInspector(); renderLineageFlow(); drawGraph(); }
function focusSelectedNeighbors() {
  const id = AMO.graph.selectedId;
  if (!id) return;
  const ids = neighborIds(id);
  const points = [...ids].map(nid => AMO.graph.positions.get(nid)).filter(Boolean);
  if (!points.length) return;
  const projected = points.map(worldToScreen).filter(p => p.visible);
  if (!projected.length) return;
  const cx = projected.reduce((sum, p) => sum + p.x, 0) / projected.length;
  const cy = projected.reduce((sum, p) => sum + p.y, 0) / projected.length;
  const rect = AMO.graph.canvas.getBoundingClientRect();
  AMO.graph.scale = Math.max(0.9, Math.min(2.8, AMO.graph.scale));
  AMO.graph.tx += rect.width / 2 - cx;
  AMO.graph.ty += rect.height / 2 - cy;
  drawGraph();
}
function renderGraphMini() {
  const n = AMO.graph.nodes.length;
  const e = AMO.graph.edges.length;
  const warnings = AMO.centralGraph.warnings || [];
  $("graphMini").innerHTML = `3D | ${n} nodes | ${e} edges${warnings.length ? ` | ${warnings.length} warnings` : ""}`;
}
function renderNodeInspector() {
  const id = AMO.graph.selectedId;
  const node = AMO.graph.nodes.find(n => nodeId(n) === id) || (AMO.centralGraph.nodes || []).find(n => nodeId(n) === id);
  if (!node) {
    $("inspectorTitle").textContent = "No node selected";
    $("inspectorKind").textContent = "Select a node to see provenance, version edges, and connected knowledge.";
    $("nodeInspector").innerHTML = "";
    return;
  }
  const edges = (AMO.centralGraph.edges || []).filter(e => edgeSource(e) === id || edgeTarget(e) === id);
  $("inspectorTitle").textContent = truncate(nodeLabel(node), 64);
  $("inspectorKind").textContent = `${nodeKind(node)} | ${nodeStatus(node)} | ${node.scope || "session"}`;
  const meta = metadata(node);
  $("nodeInspector").innerHTML = `<div class="meta-table">${metaRow("id", nodeId(node))}${metaRow("summary", nodeSummary(node))}${metaRow("session", node.session_id || "")}${metaRow("commit", node.commit_id || "")}${metaRow("evidence", node.evidence_id || "")}${metaRow("created", node.created_at || "")}</div><details open><summary>Visible edges (${edges.length})</summary><div class="edge-list">${edges.map(renderEdgeChip).join("") || `<p class="muted">No visible connected edges.</p>`}</div></details><details><summary>Metadata</summary><pre class="code-block">${escapeHtml(formatJson(meta))}</pre></details><div class="button-row"><button class="btn ghost" id="focusNodeBtn">Focus neighbors</button><button class="btn ghost" id="showProvenanceBtn">Show provenance</button></div>`;
  $("focusNodeBtn")?.addEventListener("click", focusSelectedNeighbors);
  $("showProvenanceBtn")?.addEventListener("click", () => { $("supportToggle").checked = true; buildGraph(); selectGraphNode(id); });
}
function metaRow(k, v) { return `<div class="meta-row"><span>${escapeHtml(k)}</span><span>${escapeHtml(v || "-")}</span></div>`; }
function renderEdgeChip(edge) {
  const dir = edgeSource(edge) === AMO.graph.selectedId ? "out" : "in";
  const other = dir === "out" ? edgeTarget(edge) : edgeSource(edge);
  return `<div class="edge-chip"><strong>${escapeHtml(edgeKind(edge))}</strong><br>${escapeHtml(dir)}: ${escapeHtml(other)}</div>`;
}
function renderLineageFlow() {
  const id = AMO.graph.selectedId;
  if (!id) {
    $("lineageFlow").innerHTML = "Select a graph node to see upstream evidence and downstream commit/version edges.";
    return;
  }
  const allNodes = AMO.centralGraph.nodes || [];
  const byId = new Map(allNodes.map(n => [nodeId(n), n]));
  const upstream = [];
  const downstream = [];
  (AMO.centralGraph.edges || []).forEach(edge => {
    if (edgeTarget(edge) === id) upstream.push({ edge, node: byId.get(edgeSource(edge)) });
    if (edgeSource(edge) === id) downstream.push({ edge, node: byId.get(edgeTarget(edge)) });
  });
  const current = byId.get(id) || AMO.graph.nodes.find(n => nodeId(n) === id);
  const steps = [...upstream.slice(0, 4).map(item => lineageStep(item.node, edgeKind(item.edge), false)), lineageStep(current, "selected", true), ...downstream.slice(0, 4).map(item => lineageStep(item.node, edgeKind(item.edge), false))];
  $("lineageFlow").innerHTML = steps.join("") || `<div class="muted">No visible lineage edges for this node.</div>`;
}
function lineageStep(node, edge, active) {
  if (!node) return `<div class="lineage-step" data-edge="${escapeHtml(edge)}"><strong>Missing endpoint</strong><p class="muted">Edge endpoint is outside the current graph sample.</p></div>`;
  return `<div class="lineage-step${active ? " active" : ""}" data-edge="${escapeHtml(edge)}"><strong>${escapeHtml(readableKind(nodeKind(node)))}</strong><p>${escapeHtml(truncate(nodeSummary(node) || nodeLabel(node), 120))}</p><span class="muted small">${escapeHtml(nodeStatus(node))}</span></div>`;
}
function bindEvents() {
  qsa(".nav-item").forEach(btn => btn.addEventListener("click", () => {
    if (btn.dataset.route) {
      window.location.href = btn.dataset.route;
      return;
    }
    setView(btn.dataset.view);
  }));
  qsa("[data-jump]").forEach(btn => btn.addEventListener("click", () => setView(btn.dataset.jump)));
  $("refreshBtn").addEventListener("click", refreshAll);
  $("runRetrievalBtn").addEventListener("click", runRetrieval);
  $("retrievalQuery").addEventListener("keydown", event => { if (event.key === "Enter") runRetrieval(); });
  $("globalSearch").addEventListener("keydown", event => { if (event.key === "Enter") { $("retrievalQuery").value = $("globalSearch").value; runRetrieval(); } });
  $("globalSearch").addEventListener("input", () => { if (AMO.view === "graph") buildGraph(); });
  $("graphFilter").addEventListener("input", buildGraph);
  $("kindFilter").addEventListener("change", buildGraph);
  $("statusFilter").addEventListener("change", buildGraph);
  $("graphSliceBtn").addEventListener("click", () => loadCentralGraph({ full: false }));
  $("graphFullBtn").addEventListener("click", () => loadCentralGraph({ full: true }));
  $("supportToggle").addEventListener("change", buildGraph);
  $("labelsToggle").addEventListener("change", drawGraph);
  $("drainSessionBtn").addEventListener("click", drainSelected);
  $("finalizePreviewBtn").addEventListener("click", previewFinalize);
  $("loadVersionsBtn").addEventListener("click", loadVersionFlow);
  $("versionCommitFilter").addEventListener("keydown", event => { if (event.key === "Enter") loadVersionFlow(); });
  $("versionSessionFilter").addEventListener("keydown", event => { if (event.key === "Enter") loadVersionFlow(); });
  $("consolidateBtn").addEventListener("click", () => runAdminJob("consolidate"));
  $("cacheBtn").addEventListener("click", () => runAdminJob("cache"));
  $("debugGraphBtn").addEventListener("click", () => runAdminJob("debugGraph"));
  $("debugQwenBtn").addEventListener("click", () => runAdminJob("debugQwen"));
  $("refreshJobsBtn")?.addEventListener("click", loadJobs);
  document.body.addEventListener("click", event => {
    const sessionEl = event.target.closest(".session-card");
    if (sessionEl) selectSession(sessionEl.dataset.sessionId);
    const nodeEl = event.target.closest(".node-card");
    if (nodeEl && nodeEl.dataset.nodeId) {
      setView("graph");
      $("supportToggle").checked = true;
      buildGraph();
      selectGraphNode(nodeEl.dataset.nodeId);
      focusSelectedNeighbors();
    }
    const versionNode = event.target.closest(".version-node");
    if (versionNode && versionNode.dataset.nodeId) {
      setView("graph");
      $("supportToggle").checked = true;
      buildGraph();
      selectGraphNode(versionNode.dataset.nodeId);
      focusSelectedNeighbors();
    }
    const retryJobEl = event.target.closest(".retry-job-btn");
    if (retryJobEl?.dataset.jobId) retryJob(retryJobEl.dataset.jobId);
  });
}
async function init() {
  bindEvents();
  setupGraphCanvas();
  const path = window.location.pathname;
  if (path.includes("version")) setView("versions");
  else if (path.includes("connector")) setView("connectors");
  else if (path.includes("graph")) setView("graph");
  else if (path.includes("session")) setView("sessions");
  else if (path.includes("dashboard")) setView("dashboard");
  await refreshAll();
  setInterval(() => { if (AMO.view === "dashboard" || AMO.view === "sessions") loadSessions().catch(() => {}); }, 15000);
  setInterval(() => { if (AMO.view === "admin") loadJobs().catch(() => {}); }, 12000);
}
init().catch(error => { setDaemon(false, "ui failed"); console.error(error); });
