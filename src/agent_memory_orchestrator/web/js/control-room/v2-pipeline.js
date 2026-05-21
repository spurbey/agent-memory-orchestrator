import { PIPELINE_GROUPS, V2_STAGES } from "./state.js";
import { text } from "./utils.js";

const REASONING_KINDS = new Set(["ReasoningNode", "Problem", "Decision", "Cause", "Fix", "Constraint", "OpenQuestion"]);
const V2_GRAPH_KINDS = new Set(["Packet", "Commit", "EvidenceRef", "CodeHunk", "CodeNode", "CodeVersion", "Symbol", "ReasoningNode"]);

export function jobForSession(sessionId, jobs) {
  return (jobs || []).find(job => text(job.session_id) === text(sessionId)) || null;
}

export function stageIndex(stage) {
  const index = V2_STAGES.indexOf(text(stage));
  return index < 0 ? -1 : index;
}

export function jobReached(job, stage) {
  const target = stageIndex(stage);
  if (!job || target < 0) return false;
  return stageIndex(job.last_successful_stage) >= target;
}

export function pipelineItems(counts) {
  return PIPELINE_GROUPS.map(group => ({
    ...group,
    value: counts[group.key] ?? 0,
    tone: counts[`${group.key}_tone`] || "",
  }));
}

export function globalPipelineCounts({ sessions, jobs, nodes }) {
  const rawEvents = (sessions || []).reduce((sum, row) => sum + Number(row.raw_events || 0), 0);
  const jobRows = jobs || [];
  const v2Nodes = (nodes || []).filter(node => node?.metadata?.graph_schema_version === "v2");
  return {
    raw: rawEvents,
    queue: jobRows.length,
    evidence: jobRows.filter(job => jobReached(job, "evidence_view")).length,
    packets: jobRows.filter(job => jobReached(job, "work_packets")).length,
    reason: jobRows.filter(job => jobReached(job, "reasoning_review")).length,
    graph: jobRows.filter(job => jobReached(job, "kuzu_write")).length || v2Nodes.length,
    retrieval: retrievalReadiness(jobRows),
    retrieval_tone: retrievalReadiness(jobRows) === "ready" ? "good" : "warn",
  };
}

export function sessionPipelineCounts({ timeline, job, stages }) {
  const stageRows = stages || [];
  return {
    raw: (timeline || []).length,
    queue: job ? 1 : 0,
    evidence: stageComplete(stageRows, job, "evidence_view") ? 1 : 0,
    packets: stageComplete(stageRows, job, "work_packets") ? 1 : 0,
    reason: stageComplete(stageRows, job, "reasoning_review") ? 1 : 0,
    graph: stageComplete(stageRows, job, "kuzu_write") ? 1 : 0,
    retrieval: stageComplete(stageRows, job, "faiss") || stageComplete(stageRows, job, "embeddings") ? "ready" : "pending",
    retrieval_tone: stageComplete(stageRows, job, "embeddings") ? "good" : "warn",
  };
}

export function graphNodeCounts(nodes) {
  const list = nodes || [];
  const v2 = list.filter(node => node?.metadata?.graph_schema_version === "v2");
  return {
    total: list.length,
    v2: v2.length,
    reasoning: v2.filter(node => REASONING_KINDS.has(text(node.kind))).length,
    structured: v2.filter(node => V2_GRAPH_KINDS.has(text(node.kind))).length,
  };
}

function stageComplete(stages, job, stage) {
  const row = (stages || []).find(item => item.stage === stage);
  if (row) return row.status === "complete";
  return jobReached(job, stage);
}

function retrievalReadiness(jobs) {
  if (!(jobs || []).length) return "pending";
  if ((jobs || []).some(job => jobReached(job, "embeddings") || jobReached(job, "faiss") || jobReached(job, "quality_eval"))) return "ready";
  return "pending";
}
