import { apiGet } from "../core/api.js";
import { state } from "./state.js";

export async function loadHealth() {
  state.health = await apiGet("/health");
  return state.health;
}

export async function loadSessions() {
  const data = await apiGet("/api/graph/sessions?limit=80");
  state.sessions = data.sessions || [];
  return state.sessions;
}

export async function loadJobs() {
  try {
    state.jobs = await apiGet("/api/jobs?limit=100");
  } catch (error) {
    state.jobs = { ok: false, error: error.message, jobs: [], reset_marker: null };
  }
  return state.jobs;
}

export async function loadSelectedJobDetail() {
  const job = (state.jobs.jobs || []).find(row => row.session_id === state.selectedSessionId);
  if (!job) {
    state.selectedJobDetail = null;
    return null;
  }
  state.selectedJobDetail = await apiGet(`/api/jobs/${encodeURIComponent(job.job_id)}?limit=100`);
  return state.selectedJobDetail;
}

export async function loadSessionDetail(sessionId) {
  state.selectedSessionId = sessionId;
  state.selectedSession = await apiGet(`/api/graph/session-detail?session_id=${encodeURIComponent(sessionId)}&limit=220`);
  await loadSelectedJobDetail();
  return state.selectedSession;
}

export async function loadCentralGraph({ full = false } = {}) {
  const limit = full ? 5000 : 500;
  const params = new URLSearchParams({ limit: String(limit) });
  if (full) params.set("full", "true");
  const data = await apiGet(`/api/graph/central?${params.toString()}`);
  state.centralGraph = {
    nodes: data.nodes || [],
    edges: data.edges || [],
    warnings: data.warnings || [],
    status: data.status || {},
    full: !!data.full,
    limit: data.limit || limit,
  };
  return state.centralGraph;
}

export async function loadConnectorStatus() {
  try {
    state.connectors.slack = await apiGet("/api/connectors/slack/status");
  } catch (error) {
    state.connectors.slack = { ok: false, error: error.message };
  }
  return state.connectors.slack;
}

export async function loadVersionFlow(commit = "", sessionId = "") {
  const params = new URLSearchParams({ limit: "120" });
  if (commit) params.set("commit", commit);
  if (sessionId) params.set("session_id", sessionId);
  const data = await apiGet(`/api/graph/version-flow?${params.toString()}`);
  state.versionFlow = { flows: data.flows || [], nodes: data.nodes || [], edges: data.edges || [], warnings: data.warnings || [] };
  return state.versionFlow;
}
