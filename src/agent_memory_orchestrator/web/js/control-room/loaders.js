import { apiGet } from "../core/api.js";
import { state } from "./state.js";

export async function loadHealth() {
  state.health = await apiGet("/health");
  return state.health;
}

function repoParams(params = {}) {
  const out = new URLSearchParams(params);
  if (state.selectedRepoId) out.set("repo_id", state.selectedRepoId);
  return out;
}

export async function loadRepos() {
  const data = await apiGet("/api/repos?limit=200");
  state.repos = data.repos || [];
  if (state.selectedRepoId && !state.repos.some(repo => repo.repo_id === state.selectedRepoId)) {
    state.selectedRepoId = "";
  }
  return state.repos;
}

export async function loadSessions() {
  const data = await apiGet(`/api/graph/sessions?${repoParams({ limit: "80" }).toString()}`);
  state.sessions = data.sessions || [];
  return state.sessions;
}

export async function loadJobs() {
  try {
    state.jobs = await apiGet(`/api/jobs?${repoParams({ limit: "100" }).toString()}`);
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
  const params = repoParams({ limit: String(limit) });
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
  const params = repoParams({ limit: "120" });
  if (commit) params.set("commit", commit);
  if (sessionId) params.set("session_id", sessionId);
  const data = await apiGet(`/api/graph/version-flow?${params.toString()}`);
  state.versionFlow = { flows: data.flows || [], nodes: data.nodes || [], edges: data.edges || [], warnings: data.warnings || [] };
  return state.versionFlow;
}
