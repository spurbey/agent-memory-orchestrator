import { PIPELINE_GROUPS, V2_STAGE_LABELS } from "./state.js";
import { globalPipelineCounts, graphNodeCounts, jobForSession, pipelineItems, sessionPipelineCounts } from "./v2-pipeline.js";
import {
  $,
  edgeKind,
  empty,
  escapeHtml,
  fileName,
  formatJson,
  metadata,
  nodeId,
  nodeKind,
  nodeStatus,
  nodeSummary,
  readableKind,
  statusTone,
  text,
  timeAgo,
  truncate,
} from "./utils.js";

const TRACE_EDGE_KINDS = new Set([
  "REASON_NODE_EXPLAINS_COMMIT",
  "REASON_NODE_IN_PACKET",
  "REASON_NODE_EVIDENCED_BY",
  "REASON_NODE_LINKED_TO_CODE_NODE",
  "REASON_NODE_LINKED_TO_CODE_VERSION",
  "REASON_NODE_LINKED_TO_HUNK",
  "REASON_NODE_LINKED_TO_SYMBOL",
  "COMMIT_PRODUCED_HUNK",
]);

export function setDaemon(ok, label) {
  const dot = $("daemonDot");
  if (!dot) return;
  dot.classList.toggle("good", !!ok);
  dot.classList.toggle("bad", !ok);
  $("daemonText").textContent = label;
}

export function renderHealth(state) {
  const h = state.health || {};
  const marker = h.v2_reset_marker || {};
  const rows = [
    ["daemon", h.ok ? "online" : "unknown"],
    ["graph backend", h.graph_backend],
    ["graph path", h.graph_path],
    ["qwen model", h.qwen_model],
    ["qwen extract", `${h.qwen_extract_timeout_seconds || "?"}s`],
    ["auto drain", h.auto_drain_enabled ? "enabled" : "off"],
    ["V2 marker", marker.adopted_existing_v2 ? "adopted existing stores" : marker.cleaned?.graph ? "clean reset applied" : "missing"],
    ["pipeline", marker.pipeline_version || "v2-reset-2026-05"],
  ];
  $("healthPanel").innerHTML = rows.map(([k, v]) => `<div class="health-item"><strong>${escapeHtml(v || "unknown")}</strong><span>${escapeHtml(k)}</span></div>`).join("");
}

export function renderDashboard(state) {
  const sessions = state.sessions || [];
  const nodes = state.centralGraph.nodes || [];
  const edges = state.centralGraph.edges || [];
  const jobs = state.jobs.jobs || [];
  const rawEvents = sessions.reduce((sum, row) => sum + Number(row.raw_events || 0), 0);
  const graphCounts = graphNodeCounts(nodes);
  const activeJobs = jobs.filter(job => !["complete", "failed", "pending_model"].includes(text(job.status))).length;
  const waitingJobs = jobs.filter(job => job.status === "pending_model").length;

  $("metricGrid").innerHTML = [
    metric("Sessions", sessions.length, "captured workstreams"),
    metric("Raw events", rawEvents, "append-only evidence rows"),
    metric("V2 jobs", jobs.length, `${activeJobs} active, ${waitingJobs} waiting on model`),
    metric("V2 graph", graphCounts.v2, `${edges.length} visible relations`),
  ].join("");

  renderPipeline($("pipelineStrip"), pipelineItems(globalPipelineCounts({ sessions, jobs, nodes })));
  $("recentSessions").innerHTML = sessions.slice(0, 7).map(row => sessionCard(row, state)).join("") || empty("No captured sessions yet.");
}

export function renderSessions(state) {
  const html = (state.sessions || []).map(row => sessionCard(row, state)).join("") || empty("No captured sessions yet.");
  $("sessionList").innerHTML = html;
  $("recentSessions").innerHTML = (state.sessions || []).slice(0, 7).map(row => sessionCard(row, state)).join("") || empty("No captured sessions yet.");
}

export function renderSessionDetail(state) {
  const data = state.selectedSession;
  if (!data) return;
  $("sessionEmpty").classList.add("hidden");
  $("sessionContent").classList.remove("hidden");

  const timeline = data.timeline || [];
  const job = jobForSession(data.session_id, state.jobs.jobs || []);
  const detail = state.selectedJobDetail || {};
  const stages = detail.stages || [];
  const events = detail.events || [];
  const status = job?.status || "not queued";

  $("sessionSource").textContent = "Closed-session V2 job";
  $("sessionTitle").textContent = data.session_id;
  $("sessionSummary").textContent = job
    ? `${timeline.length} raw events, job ${status}, current stage ${job.current_stage || "-"}, last complete ${job.last_successful_stage || "-"}`
    : `${timeline.length} raw events. This session is not queued yet; a later session_start boundary must close it first.`;
  $("scanEvidenceBtn").textContent = "Scan / Enqueue";
  $("retrySelectedJobBtn").classList.toggle("hidden", !job || !["failed", "pending_model"].includes(status));

  renderPipeline($("sessionPipeline"), pipelineItems(sessionPipelineCounts({ timeline, job, stages })));
  $("timelineList").innerHTML = timeline.map(renderTimeline).join("") || empty("No raw evidence for this session.");
  $("selectedJobPanel").innerHTML = renderSelectedJob(job, detail);
  $("jobStageRows").innerHTML = renderStageRows(stages, job);
  $("jobArtifacts").innerHTML = renderArtifacts(stages, job);
  $("jobEventLog").innerHTML = events.map(renderJobEvent).join("") || empty("No V2 job events recorded yet.");
}

export function renderJobs(state) {
  const marker = state.jobs?.reset_marker;
  const markerTarget = $("v2ResetMarker");
  if (markerTarget) markerTarget.innerHTML = renderMarker(marker);

  const list = $("v2JobsList");
  if (!list) return;
  if (!state.jobs?.ok) {
    list.innerHTML = `<pre class="code-block">${escapeHtml(state.jobs?.error || "Unable to load jobs")}</pre>`;
    return;
  }
  const jobs = state.jobs.jobs || [];
  list.innerHTML = jobs.map(renderJobCard).join("") || empty("No V2 session jobs yet.");
}

export function renderConnectorStatus(state) {
  const target = $("slackStatusPanel");
  if (!target) return;
  const data = state.connectors.slack;
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
  target.innerHTML = `<div class="connector-card">
    <div class="panel-head"><div><span class="pill ${config.enabled ? "good" : "warn"}">Slack</span><h3>Local Socket Mode</h3></div><span class="pill blue">mention-only</span></div>
    <div class="connector-grid">
      ${connectorKv("enabled", config.enabled ? "yes" : "no", config.enabled ? "good" : "warn")}
      ${connectorKv("team", config.team_id || "not set", config.team_id ? "good" : "warn")}
      ${connectorKv("bot user", config.bot_user_id || "not set", config.bot_user_id ? "good" : "warn")}
      ${connectorKv("app token", tokens.app_token ? "present" : "missing", tokens.app_token ? "good" : "bad")}
      ${connectorKv("bot token", tokens.bot_token ? "present" : "missing", tokens.bot_token ? "good" : "bad")}
      ${connectorKv("reply mode", "tagged answer", "good")}
    </div>
    <p class="muted">${escapeHtml(data.behavior || "Answers only when tagged.")}</p>
  </div>`;
  if ($("slackCommandPanel") && data.run_command) $("slackCommandPanel").textContent = `amo-cli slack setup-wizard\n${data.run_command}`;
}

export function renderVersionFlow(state) {
  const flows = state.versionFlow?.flows || [];
  const warnings = state.versionFlow?.warnings || [];
  $("versionWarnings").innerHTML = warnings.map(w => `<div class="warning-card">${escapeHtml(w)}</div>`).join("");
  $("versionFlowList").innerHTML = flows.map(renderVersionFlowCard).join("") || empty("No committed version flows found yet.");
}

export function renderRetrievalResult(result) {
  if (result?.ok === false) {
    $("retrievalResult").innerHTML = `<section class="panel"><h2>V2 retrieval is not ready</h2><p class="muted">${escapeHtml(result.error || "Unknown retrieval error")}</p>${result.hint ? `<p class="muted">${escapeHtml(result.hint)}</p>` : ""}<pre class="code-block">${escapeHtml(formatJson({ graph_path: result.graph_path, db_path: result.db_path }))}</pre></section>`;
    return;
  }
  const retrieval = result.retrieval || {};
  const answer = result.answer || {};
  const hits = retrieval.hits || [];
  const citations = answer.citations || [];
  $("retrievalResult").innerHTML = `
    <section class="panel">
      <div class="result-grid">
        <div>
          <p class="eyebrow">Indexed V2 retrieval</p>
          <h2>${escapeHtml(retrieval.intent || "general")}</h2>
          <p class="muted">${escapeHtml(result.graph_scope || "v2 graph")} | ${escapeHtml(truncate(result.db_path || "", 84))}</p>
          <div class="session-meta">
            <span class="pill ${String(retrieval.vector_status || "").includes("completed") ? "good" : "warn"}">vector ${escapeHtml(retrieval.vector_status || "unknown")}</span>
            <span class="pill blue">${escapeHtml(retrieval.reranker || "deterministic")}</span>
            <span class="pill good">${escapeHtml(hits.length)} hits</span>
          </div>
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

function metric(label, value, caption) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><span>${escapeHtml(caption)}</span></div>`;
}

function renderPipeline(target, items) {
  if (!target) return;
  target.innerHTML = (items || PIPELINE_GROUPS).map(item => `<div class="stage-card ${escapeHtml(item.tone || "")}"><strong>${escapeHtml(item.label)}</strong><div class="count">${escapeHtml(item.value ?? 0)}</div><small>${escapeHtml(item.desc)}</small></div>`).join("");
}

function sessionCard(row, state) {
  const id = text(row.session_id);
  const selected = id === state.selectedSessionId ? " active" : "";
  const job = jobForSession(id, state.jobs.jobs || []);
  const sources = (row.source_apps || []).join(", ") || "unknown";
  const status = job?.status || "not queued";
  return `<article class="session-card${selected}" data-session-id="${escapeHtml(id)}">
    <div class="id">${escapeHtml(id)}</div>
    <div class="muted small">${escapeHtml(truncate(row.cwd || row.repo || "local session", 72))}</div>
    <div class="session-meta">
      <span class="pill good">${Number(row.raw_events || 0)} raw</span>
      <span class="pill blue">${escapeHtml(row.latest_event || "event")}</span>
      <span class="pill">${escapeHtml(sources)}</span>
      <span class="pill ${statusTone(status)}">${escapeHtml(status)}</span>
      ${job?.current_stage ? `<span class="pill">${escapeHtml(job.current_stage)}</span>` : ""}
      <span class="pill">${escapeHtml(timeAgo(row.latest_at))}</span>
    </div>
  </article>`;
}

function renderSelectedJob(job, detail) {
  if (!job) return empty("This session has no V2 job yet. Start a newer session or run Scan / Enqueue after a session_start boundary exists.");
  const stages = detail.stages || [];
  return `<article class="job-card selected-job">
    <div class="panel-head">
      <div>
        <p class="eyebrow">V2 job</p>
        <h3>${escapeHtml(job.job_id)}</h3>
      </div>
      <span class="pill ${statusTone(job.status)}">${escapeHtml(job.status)}</span>
    </div>
    <div class="job-meta">
      <span>current ${escapeHtml(job.current_stage || "-")}</span>
      <span>last ${escapeHtml(job.last_successful_stage || "-")}</span>
      <span>attempts ${escapeHtml(job.attempt_count || 0)}</span>
      <span>${escapeHtml(truncate(job.artifact_dir || "", 110))}</span>
    </div>
    <p class="muted">${escapeHtml(stages.length)} recorded stages. Artifacts are stored under the job directory; raw JSONL remains the source of truth.</p>
  </article>`;
}

function renderStageRows(stages, job) {
  if (!job) return empty("No job stage rows yet.");
  if (!(stages || []).length) return empty("The job exists but has not started a stage yet.");
  return `<div class="stage-table">${stages.map(stage => `<article class="stage-row">
    <div>
      <strong>${escapeHtml(V2_STAGE_LABELS[stage.stage] || readableKind(stage.stage))}</strong>
      <p class="muted small">${escapeHtml(stage.stage)}</p>
    </div>
    <span class="pill ${statusTone(stage.status)}">${escapeHtml(stage.status)}</span>
    <div class="stage-artifacts">
      ${stage.input_artifact ? `<span>in ${escapeHtml(fileName(stage.input_artifact))}</span>` : ""}
      ${stage.output_artifact ? `<span>out ${escapeHtml(fileName(stage.output_artifact))}</span>` : ""}
    </div>
  </article>`).join("")}</div>`;
}

function renderArtifacts(stages, job) {
  if (!job) return empty("No V2 artifact directory exists for this session yet.");
  const rows = (stages || []).filter(stage => stage.output_artifact);
  return `<div class="artifact-list">
    <article class="artifact-card"><strong>Artifact directory</strong><p class="muted small">${escapeHtml(job.artifact_dir || "")}</p></article>
    ${rows.map(stage => `<article class="artifact-card">
      <span class="pill ${statusTone(stage.status)}">${escapeHtml(V2_STAGE_LABELS[stage.stage] || stage.stage)}</span>
      <p>${escapeHtml(stage.output_artifact)}</p>
      ${stage.output_hash ? `<p class="muted small">sha256 ${escapeHtml(truncate(stage.output_hash, 32))}</p>` : ""}
    </article>`).join("")}
  </div>`;
}

function renderTimeline(row) {
  return `<article class="timeline-item"><div class="panel-head"><strong>${escapeHtml(row.event_name || "event")}</strong><span class="pill">${escapeHtml(timeAgo(row.created_at))}</span></div><div class="muted small">evidence ${escapeHtml(row.evidence_id || row.id || "")}</div><pre class="code-block">${escapeHtml(formatJson(row.payload || row))}</pre></article>`;
}

function renderJobEvent(event) {
  return `<article class="job-event">
    <div class="panel-head"><strong>${escapeHtml(event.event_type || "event")}</strong><span class="pill">${escapeHtml(timeAgo(event.created_at))}</span></div>
    <p class="muted">${escapeHtml(event.stage || "job")} - ${escapeHtml(event.message || "")}</p>
    <pre class="code-block small">${escapeHtml(formatJson(event.metadata || {}))}</pre>
  </article>`;
}

function renderJobCard(job) {
  const status = text(job.status || "unknown");
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
      <div class="button-row"><span class="pill ${statusTone(status)}">${escapeHtml(status)}</span>${retry}</div>
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

function renderMarker(marker) {
  if (!marker) return `<span class="pill warn">V2 production marker missing</span><span class="pill">run reset or adopt before V2 graph writes</span>`;
  if (marker.fresh_install) {
    return `<span class="pill good">Fresh V2 stores ready</span><span class="pill blue">${escapeHtml(marker.pipeline_version || "v2")}</span><span class="pill">no pre-V2 graph cleanup needed</span>`;
  }
  if (marker.adopted_existing_v2) {
    const docs = marker.validation?.retrieval?.retrieval_document_count;
    return `<span class="pill good">V2 adopted existing stores</span><span class="pill blue">${escapeHtml(marker.pipeline_version || "v2")}</span>${docs ? `<span class="pill">${escapeHtml(docs)} retrieval docs at adoption</span>` : ""}`;
  }
  return `<span class="pill good">V2 clean reset applied</span><span class="pill blue">${escapeHtml(marker.pipeline_version || "v2")}</span>`;
}

function connectorKv(label, value, tone) {
  return `<div class="connector-kv"><span>${escapeHtml(label)}</span><strong class="${escapeHtml(tone)}">${escapeHtml(value)}</strong></div>`;
}

function renderVersionFlowCard(flow) {
  const commit = flow.commit_node || {};
  const counts = flow.counts || {};
  return `<article class="version-card">
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
      </div>
    </div>
  </article>`;
}

function renderCitationCard(citation) {
  return `<article class="retrieval-card">
    <div class="panel-head"><div><p class="eyebrow">rank ${escapeHtml(citation.rank || "")}</p><h3>${escapeHtml(citation.packet_id || citation.graph_node_id || citation.doc_id || "citation")}</h3></div><span class="pill blue">${escapeHtml(citation.doc_type || "doc")}</span></div>
    <p>${escapeHtml(truncate(citation.title || citation.snippet || "", 240))}</p>
    <pre class="code-block small">${escapeHtml(formatJson(citation.trace || {}))}</pre>
  </article>`;
}

function renderRetrievalHitCard(hit) {
  const doc = hit.document || {};
  return `<article class="retrieval-card">
    <div class="panel-head"><div><p class="eyebrow">${escapeHtml(doc.doc_type || "doc")}</p><h3>${escapeHtml(doc.title || doc.doc_id || "hit")}</h3></div><span class="pill good">${escapeHtml(Number(hit.score || 0).toFixed(3))}</span></div>
    <p>${escapeHtml(truncate(doc.body || "", 420))}</p>
    <div class="session-meta">
      <span class="pill">${escapeHtml(doc.packet_id || "no packet")}</span>
      <span class="pill blue">${escapeHtml(doc.commit_sha || "no commit")}</span>
      <span class="pill">${escapeHtml((hit.sources || []).join(", ") || "ranked")}</span>
    </div>
  </article>`;
}
