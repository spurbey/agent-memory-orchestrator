import { state } from "./state.js";
import { $, qsa } from "./utils.js";
import {
  renderConnectorStatus,
  renderDashboard,
  renderHealth,
  renderJobs,
  renderSessionDetail,
  renderSessions,
  setDaemon,
  renderVersionFlow,
} from "./renderers.js";
import {
  loadCentralGraph,
  loadConnectorStatus,
  loadHealth,
  loadJobs,
  loadSessions,
  loadVersionFlow,
} from "./loaders.js";
import {
  retryJob,
  retrySelectedJob,
  runAdminJob,
  runRetrieval,
  scanEvidenceForSelectedSession,
  selectSession,
} from "./actions.js";

function setView(view) {
  state.view = view;
  qsa(".view").forEach(el => el.classList.toggle("active", el.id === `${view}View`));
  qsa(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  const copy = {
    dashboard: ["Dashboard", "V2 production status from raw capture to answer-grade retrieval."],
    sessions: ["Sessions", "Inspect raw capture, closed-session job state, artifacts, and V2 stage events."],
    versions: ["Versions", "Inspect commit-centered memory flows."],
    retrieval: ["Retrieval", "Run the indexed V2 GraphRAG path and inspect citations."],
    connectors: ["Connectors", "Check local connector readiness and external message capture."],
    admin: ["Admin", "Operate V2 session jobs and daemon diagnostics."],
  }[view] || ["AMO", "Local GraphRAG control room"];
  $("pageTitle").textContent = copy[0];
  $("pageSubtitle").textContent = copy[1];
}

async function refreshAll() {
  await Promise.allSettled([
    loadHealth(),
    loadJobs(),
    loadSessions(),
    loadCentralGraph(),
    loadConnectorStatus(),
    loadVersionFlow(),
  ]);
  setDaemon(!!state.health?.ok, `daemon on ${state.health?.graph_backend || "graph"}`);
  renderHealth(state);
  renderJobs(state);
  renderSessions(state);
  renderDashboard(state);
  renderConnectorStatus(state);
  renderVersionFlow(state);
  if (!state.selectedSessionId && state.sessions[0]) {
    await selectSession(state.sessions[0].session_id, { silent: true, setView });
  } else if (state.selectedSessionId) {
    await selectSession(state.selectedSessionId, { silent: true, setView });
  }
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
  $("runRetrievalBtn").addEventListener("click", () => runRetrieval(setView));
  $("retrievalQuery").addEventListener("keydown", event => { if (event.key === "Enter") runRetrieval(setView); });
  $("globalSearch").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      $("retrievalQuery").value = $("globalSearch").value;
      runRetrieval(setView);
    }
  });
  $("scanEvidenceBtn").addEventListener("click", scanEvidenceForSelectedSession);
  $("retrySelectedJobBtn").addEventListener("click", retrySelectedJob);
  $("loadVersionsBtn").addEventListener("click", async () => {
    await loadVersionFlow($("versionCommitFilter").value.trim(), $("versionSessionFilter").value.trim());
    renderVersionFlow(state);
  });
  $("versionCommitFilter").addEventListener("keydown", event => { if (event.key === "Enter") $("loadVersionsBtn").click(); });
  $("versionSessionFilter").addEventListener("keydown", event => { if (event.key === "Enter") $("loadVersionsBtn").click(); });
  $("consolidateBtn").addEventListener("click", () => runAdminJob("consolidate"));
  $("cacheBtn").addEventListener("click", () => runAdminJob("cache"));
  $("debugGraphBtn").addEventListener("click", () => runAdminJob("debugGraph"));
  $("debugQwenBtn").addEventListener("click", () => runAdminJob("debugQwen"));
  $("refreshJobsBtn").addEventListener("click", async () => { await loadJobs(); renderJobs(state); });

  document.body.addEventListener("click", event => {
    const sessionEl = event.target.closest(".session-card");
    if (sessionEl?.dataset.sessionId) selectSession(sessionEl.dataset.sessionId, { setView });
    const retryJobEl = event.target.closest(".retry-job-btn");
    if (retryJobEl?.dataset.jobId) retryJob(retryJobEl.dataset.jobId);
  });
}

async function init() {
  bindEvents();
  const path = window.location.pathname;
  if (path.includes("version")) setView("versions");
  else if (path.includes("connector")) setView("connectors");
  else if (path.includes("session")) setView("sessions");
  else if (path.includes("dashboard")) setView("dashboard");
  else setView("dashboard");
  await refreshAll();
  setInterval(() => {
    if (state.view === "dashboard" || state.view === "sessions") {
      Promise.allSettled([loadSessions(), loadJobs()]).then(() => {
        renderSessions(state);
        renderDashboard(state);
        if (state.selectedSession) renderSessionDetail(state);
      });
    }
  }, 15000);
  setInterval(() => {
    if (state.view === "admin") loadJobs().then(() => renderJobs(state)).catch(() => {});
  }, 12000);
}

init().catch(error => {
  setDaemon(false, "ui failed");
  console.error(error);
});
