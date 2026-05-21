export const state = {
  view: "dashboard",
  sessions: [],
  selectedSessionId: "",
  selectedSession: null,
  selectedJobDetail: null,
  health: null,
  jobs: { ok: false, jobs: [], reset_marker: null },
  connectors: { slack: null },
  centralGraph: { nodes: [], edges: [], warnings: [], full: false, limit: 360 },
  versionFlow: { flows: [], nodes: [], edges: [], warnings: [] },
};

export const V2_STAGES = [
  "evidence_view",
  "work_packets",
  "qwen_reasoning",
  "reasoning_review",
  "git_hunks",
  "ast_code_nodes",
  "symbol_versions",
  "reasoning_code_links",
  "kuzu_write",
  "retrieval_docs",
  "embeddings",
  "faiss",
  "quality_eval",
];

export const V2_STAGE_LABELS = {
  evidence_view: "Evidence View",
  work_packets: "Work Packets",
  qwen_reasoning: "Qwen Reasoning",
  reasoning_review: "Reasoning Review",
  git_hunks: "Git Hunks",
  ast_code_nodes: "AST Code Nodes",
  symbol_versions: "Symbol Versions",
  reasoning_code_links: "Reason-Code Links",
  kuzu_write: "Kuzu Write",
  retrieval_docs: "Retrieval Docs",
  embeddings: "Embeddings",
  faiss: "FAISS",
  quality_eval: "Quality Eval",
};

export const PIPELINE_GROUPS = [
  { key: "raw", label: "Raw", desc: "Hook JSONL capture" },
  { key: "queue", label: "Queue", desc: "Closed-session jobs" },
  { key: "evidence", label: "Evidence", desc: "Cleaned evidence view" },
  { key: "packets", label: "Packets", desc: "Commit-backed packets" },
  { key: "reason", label: "Reason", desc: "Qwen + review" },
  { key: "graph", label: "Graph", desc: "V2 Kuzu writes" },
  { key: "retrieval", label: "Retrieve", desc: "Docs + vectors" },
];
