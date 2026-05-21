export const ANSWER_KINDS = new Set([
  "Decision", "Fix", "Bug", "Blocker", "TestRun", "Topic", "Cluster",
  "ReasoningNode", "Problem", "Cause", "Constraint", "OpenQuestion", "Commit", "Packet", "CodeNode", "CodeVersion", "Symbol",
]);

export const SUPPORT_KINDS = new Set([
  "EvidenceRef", "RawEvidenceRef", "Session", "Repo", "Branch", "File", "App", "ToolResult", "Prompt",
]);

export const SEMANTIC_EDGE_TYPES = new Set([
  "CAUSES", "RESOLVES", "HAS_REASONING_NODE", "EXPLAINS_CODE", "EXTRACTED_FROM_COMMIT", "TOUCHES_CODE", "SUPPORTS_ANSWER",
]);

export const VERSION_EDGE_TYPES = new Set([
  "COMMITTED_AS", "REFINES", "SUPERSEDES", "DUPLICATE_OF", "CONTRADICTS", "VALIDATED_BY", "MERGED_INTO",
  "REASON_NODE_EXPLAINS_COMMIT", "REASON_NODE_IN_PACKET", "COMMIT_PRODUCED_HUNK",
]);

const NODE_STYLE = {
  Query: { color: "#fff3a3", halo: "#f2cf78", shape: "ring", radius: 12, tier: 7 },
  Answer: { color: "#f7fbff", halo: "#ffffff", shape: "ring", radius: 11, tier: 7 },
  Decision: { color: "#80dec6", halo: "#80dec6", shape: "diamond", radius: 8, tier: 6 },
  Fix: { color: "#b7f56e", halo: "#b7f56e", shape: "circle", radius: 8, tier: 6 },
  Bug: { color: "#ff766f", halo: "#ff766f", shape: "triangle", radius: 8, tier: 6 },
  Problem: { color: "#ff9d6e", halo: "#ff766f", shape: "triangle", radius: 8, tier: 6 },
  Cause: { color: "#f2cf78", halo: "#f2cf78", shape: "circle", radius: 7, tier: 5 },
  Constraint: { color: "#8ab5ff", halo: "#8ab5ff", shape: "square", radius: 7, tier: 5 },
  OpenQuestion: { color: "#bda2ff", halo: "#bda2ff", shape: "ring", radius: 7, tier: 5 },
  TestRun: { color: "#8ab5ff", halo: "#8ab5ff", shape: "square", radius: 7, tier: 5 },
  ReasoningNode: { color: "#80dec6", halo: "#80dec6", shape: "diamond", radius: 7, tier: 5 },
  Packet: { color: "#61d6ff", halo: "#61d6ff", shape: "hex", radius: 7, tier: 4 },
  GitCommit: { color: "#ffffff", halo: "#bda2ff", shape: "box", radius: 7, tier: 4 },
  Commit: { color: "#ffffff", halo: "#bda2ff", shape: "box", radius: 7, tier: 4 },
  CodeNode: { color: "#bda2ff", halo: "#bda2ff", shape: "file", radius: 6.5, tier: 4 },
  CodeVersion: { color: "#bda2ff", halo: "#bda2ff", shape: "file", radius: 6.5, tier: 4 },
  Symbol: { color: "#c7b8ff", halo: "#bda2ff", shape: "file", radius: 6.3, tier: 4 },
  Topic: { color: "#bda2ff", halo: "#bda2ff", shape: "ring", radius: 10, tier: 3 },
  Cluster: { color: "#bda2ff", halo: "#bda2ff", shape: "ring", radius: 12, tier: 3 },
  EvidenceRef: { color: "#a5d7c4", halo: "#a5d7c4", shape: "square", radius: 4.8, tier: 2 },
  RawEvidenceRef: { color: "#67786f", halo: "#67786f", shape: "circle", radius: 4.1, tier: 1 },
  ToolResult: { color: "#87968f", halo: "#87968f", shape: "circle", radius: 4.3, tier: 1 },
  Prompt: { color: "#91cf7b", halo: "#91cf7b", shape: "circle", radius: 4.6, tier: 1 },
  Session: { color: "#96fff0", halo: "#80dec6", shape: "ring", radius: 10, tier: 1 },
  Repo: { color: "#91cf7b", halo: "#91cf7b", shape: "box", radius: 6, tier: 1 },
  File: { color: "#91a69b", halo: "#91a69b", shape: "file", radius: 5, tier: 1 },
};

const EDGE_STYLE = {
  CAUSES: { color: "#ff9d6e", width: 2.2, particles: true, dash: [] },
  RESOLVES: { color: "#b7f56e", width: 2.4, particles: true, dash: [] },
  CREATED: { color: "#80dec6", width: 1.8, particles: true, dash: [] },
  EXTRACTED_AS: { color: "#80dec6", width: 1.8, particles: true, dash: [8, 8] },
  HAS_REASONING_NODE: { color: "#61d6ff", width: 1.8, particles: true, dash: [] },
  EXPLAINS_CODE: { color: "#bda2ff", width: 2.0, particles: true, dash: [] },
  TOUCHES_CODE: { color: "#bda2ff", width: 1.7, particles: false, dash: [5, 7] },
  COMMITTED_AS: { color: "#ffffff", width: 2.0, particles: true, dash: [] },
  EXTRACTED_FROM_COMMIT: { color: "#ffffff", width: 1.6, particles: true, dash: [7, 5] },
  SUPPORTS_ANSWER: { color: "#f2cf78", width: 2.4, particles: true, dash: [] },
  SIMILAR_TO: { color: "#5f756b", width: 0.9, particles: false, dash: [3, 9] },
  PART_OF: { color: "#668b7a", width: 1.1, particles: false, dash: [] },
  HAS_TURN: { color: "#668b7a", width: 0.9, particles: false, dash: [] },
};

export function nodeId(node) {
  return String(node?.id || node?.node_id || "");
}

export function nodeKind(node) {
  return String(node?.kind || node?.type || "Node");
}

export function nodeStatus(node) {
  return String(node?.status || "draft");
}

export function nodeLabel(node) {
  return String(node?.label || node?.summary || nodeId(node));
}

export function nodeSummary(node) {
  return String(node?.summary || node?.label || "");
}

export function nodeMetadata(node) {
  return node && typeof node.metadata === "object" && node.metadata ? node.metadata : {};
}

export function edgeSource(edge) {
  return String(edge?.source_id || edge?.source || edge?.from || edge?.sourceId || "");
}

export function edgeTarget(edge) {
  return String(edge?.target_id || edge?.target || edge?.to || edge?.targetId || "");
}

export function edgeKind(edge) {
  return String(edge?.kind || edge?.relation || edge?.label || "RELATED");
}

export function isSupportNode(node) {
  return SUPPORT_KINDS.has(nodeKind(node));
}

export function isAnswerNode(node) {
  const kind = nodeKind(node);
  if (isSupportNode(node)) return false;
  const status = nodeStatus(node);
  return ANSWER_KINDS.has(kind) || node?.scope === "central" || ["committed", "active", "session_final", "accepted"].includes(status);
}

export function styleForNode(node) {
  const kind = nodeKind(node);
  const base = NODE_STYLE[kind] || { color: "#9fb5aa", halo: "#9fb5aa", shape: "circle", radius: 5.8, tier: 3 };
  if (nodeStatus(node) === "superseded") return { ...base, color: "#5f6b65", halo: "#5f6b65" };
  return base;
}

export function styleForEdge(edge) {
  const kind = edgeKind(edge);
  if (EDGE_STYLE[kind]) return EDGE_STYLE[kind];
  if (VERSION_EDGE_TYPES.has(kind)) return { color: "#b7f56e", width: 1.8, particles: true, dash: [] };
  if (SEMANTIC_EDGE_TYPES.has(kind)) return { color: "#80dec6", width: 1.7, particles: true, dash: [] };
  return { color: "#668b7a", width: 1.0, particles: false, dash: [] };
}

export function graphClassForNode(node) {
  const kind = nodeKind(node);
  if (["Decision", "Fix", "Problem", "Cause", "Constraint", "ReasoningNode"].includes(kind)) return "reasoning";
  if (["Packet", "EvidenceRef", "RawEvidenceRef", "ToolResult", "Prompt"].includes(kind)) return "evidence";
  if (["Commit", "GitCommit", "CodeNode", "CodeVersion", "Symbol", "File"].includes(kind)) return "code";
  if (["Query", "Answer"].includes(kind)) return "retrieval";
  return "memory";
}
