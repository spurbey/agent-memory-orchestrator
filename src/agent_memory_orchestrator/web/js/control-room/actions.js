import { apiGet, apiPost } from "../core/api.js";
import { state } from "./state.js";
import { $, escapeHtml, formatJson, text } from "./utils.js";
import { loadCentralGraph, loadJobs, loadSelectedJobDetail, loadSessionDetail, loadSessions } from "./loaders.js";
import { renderDashboard, renderJobs, renderRetrievalResult, renderSessionDetail, renderSessions } from "./renderers.js";

export async function scanEvidenceForSelectedSession() {
  if (!state.selectedSessionId) return;
  const output = $("jobEventLog");
  if (output) output.insertAdjacentHTML("afterbegin", `<article class="job-event"><strong>Scanning raw evidence...</strong></article>`);
  const result = await apiPost("/graph/drain", { session_id: state.selectedSessionId, limit: 1000, max_windows: 5 });
  await loadJobs();
  await loadSelectedJobDetail();
  renderJobs(state);
  renderSessionDetail(state);
  if (output) output.insertAdjacentHTML("afterbegin", `<article class="job-event"><span class="pill good">scan result</span><pre class="code-block small">${escapeHtml(formatJson(result))}</pre></article>`);
}

export async function retrySelectedJob() {
  const job = (state.jobs.jobs || []).find(row => row.session_id === state.selectedSessionId);
  if (!job) return;
  await retryJob(job.job_id);
  await loadSessionDetail(state.selectedSessionId);
  renderSessionDetail(state);
}

export async function retryJob(jobId) {
  const output = $("adminOutput");
  if (output) output.textContent = `Retrying ${jobId}...`;
  const result = await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { forced_by: "dashboard" });
  if (output) output.textContent = formatJson(result);
  await loadJobs();
  renderJobs(state);
}

export async function runRetrieval(setView) {
  const query = $("retrievalQuery").value.trim() || $("globalSearch").value.trim();
  if (!query) return;
  $("retrievalResult").innerHTML = `<section class="panel"><p class="muted">Searching production graph memory...</p></section>`;
  setView("retrieval");
  try {
    const result = await apiPost("/graph/retrieve", {
      query,
      repo_id: state.selectedRepoId || "",
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

export async function runAdminJob(kind) {
  const output = $("adminOutput");
  output.textContent = `Running ${kind}...`;
  try {
    let result;
    if (kind === "debugGraph") result = await apiGet("/api/debug/graph?limit=50");
    if (kind === "debugQwen") result = await apiGet("/api/debug/qwen");
    output.textContent = formatJson(result || { ok: false, error: `unknown job ${kind}` });
    await Promise.allSettled([loadSessions(), loadCentralGraph(), loadJobs()]);
    renderSessions(state);
    renderDashboard(state);
    renderJobs(state);
  } catch (error) {
    output.textContent = error.stack || error.message;
  }
}

export async function selectSession(sessionId, { silent = false, setView } = {}) {
  state.selectedSessionId = text(sessionId);
  renderSessions(state);
  try {
    await loadSessionDetail(state.selectedSessionId);
    renderSessions(state);
    renderSessionDetail(state);
    if (!silent && setView) setView("sessions");
  } catch (error) {
    state.selectedSession = null;
    $("sessionContent").classList.add("hidden");
    $("sessionEmpty").classList.remove("hidden");
    $("sessionEmpty").innerHTML = `<h2>Session failed to load</h2><p>${escapeHtml(error.message)}</p>`;
  }
}
