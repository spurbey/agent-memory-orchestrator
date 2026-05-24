import { apiGet, apiPost } from "../core/api.js";
import { $, escapeHtml, formatJson, qsa, truncate } from "../core/dom.js";
import { ensurePositions, simulateLayout } from "./layout.js";
import { collectNeighbors, drawScene, nodeAtPoint, prepareContext, projectPoint, resizeCanvas } from "./render.js";
import {
  edgeKind,
  edgeSource,
  edgeTarget,
  graphClassForNode,
  isAnswerNode,
  nodeId,
  nodeKind,
  nodeLabel,
  nodeMetadata,
  nodeStatus,
  nodeSummary,
  styleForEdge,
  styleForNode,
} from "./semantics.js";

const state = {
  canvas: null,
  ctx: null,
  repos: [],
  repoId: "",
  centralGraph: { nodes: [], edges: [], warnings: [], full: false, limit: 500 },
  visibleNodes: [],
  visibleEdges: [],
  positions: new Map(),
  velocities: new Map(),
  screenPositions: new Map(),
  traceNodeIds: new Set(),
  selectedId: "",
  hoveredId: "",
  mode: "atlas",
  graphLimit: 500,
  showSupport: false,
  showLabels: false,
  showAxes: false,
  renderCap: 420,
  cappedOut: 0,
  layoutTicks: 0,
  scale: 1,
  tx: 0,
  ty: 0,
  rotationX: -0.48,
  rotationY: 0.72,
  cameraDistance: 980,
  fov: 720,
  dragging: false,
  dragMode: "rotate",
  dragStart: null,
  spaceDown: false,
  running: false,
  traceStages: [],
  traceStageIndex: 0,
  lastFrame: 0,
};

async function loadRepos() {
  const data = await apiGet("/api/repos?limit=200");
  state.repos = data.repos || [];
  if (state.repoId && !state.repos.some(repo => repo.repo_id === state.repoId)) state.repoId = "";
  renderRepoScope();
}

function renderRepoScope() {
  const select = $("repoScopeSelect");
  if (!select) return;
  select.innerHTML = [
    `<option value="">All repositories</option>`,
    ...state.repos.map(repo => `<option value="${escapeHtml(repo.repo_id)}">${escapeHtml(repoLabel(repo))}</option>`),
  ].join("");
  select.value = state.repoId || "";
}

function repoLabel(repo) {
  const path = String(repo.repo_path || "");
  const leaf = path.split(/[\\/]/).filter(Boolean).pop();
  const name = leaf || truncate(repo.repo_id || "repo", 42);
  const count = Number(repo.job_count || 0);
  const nodes = Number(repo.node_count || 0);
  return `${name} - ${count ? `${count} jobs` : nodes ? `${nodes} nodes` : "repo"}`;
}

function setStatus(message, tone = "") {
  const target = $("graphStatus");
  if (!target) return;
  target.textContent = message;
  target.className = `status-chip ${tone}`.trim();
}

async function loadGraph({ full = state.centralGraph.full } = {}) {
  const limit = full ? 5000 : state.graphLimit;
  const params = new URLSearchParams({ limit: String(limit) });
  if (full) params.set("full", "true");
  if (state.repoId) params.set("repo_id", state.repoId);
  setStatus("loading graph", "warn");
  const data = await apiGet(`/api/graph/central?${params.toString()}`);
  state.centralGraph = {
    nodes: data.nodes || [],
    edges: data.edges || [],
    warnings: data.warnings || [],
    status: data.status || {},
    full: !!data.full,
    limit: data.limit || limit,
  };
  fillFilters();
  rebuildGraph();
  updateStats();
  setStatus(`${state.centralGraph.nodes.length} nodes / ${state.centralGraph.edges.length} edges`, "good");
}

function fillFilters() {
  const nodes = state.centralGraph.nodes || [];
  const kinds = [...new Set(nodes.map(nodeKind))].sort();
  const statuses = [...new Set(nodes.map(nodeStatus))].sort();
  $("kindFilter").innerHTML = `<option value="">All kinds</option>${kinds.map(kind => `<option>${escapeHtml(kind)}</option>`).join("")}`;
  $("statusFilter").innerHTML = `<option value="">All status</option>${statuses.map(status => `<option>${escapeHtml(status)}</option>`).join("")}`;
}

function rebuildGraph() {
  const query = ($("graphFilter")?.value || "").trim().toLowerCase();
  const kind = $("kindFilter")?.value || "";
  const status = $("statusFilter")?.value || "";
  const mode = state.mode;
  const rawNodes = (state.centralGraph.nodes || []).filter(node => {
    if (!state.showSupport && !isAnswerNode(node)) return false;
    if (kind && nodeKind(node) !== kind) return false;
    if (status && nodeStatus(node) !== status) return false;
    if (mode === "causal" && !["EvidenceRef", "RawEvidenceRef", "Packet", "ReasoningNode", "Decision", "Fix", "Commit", "GitCommit", "CodeNode", "CodeVersion", "Symbol"].includes(nodeKind(node))) return false;
    if (mode === "similarity" && !["ReasoningNode", "Decision", "CodeNode", "CodeVersion", "Symbol", "Packet", "Topic", "Cluster"].includes(nodeKind(node))) return false;
    if (query) {
      const haystack = `${nodeKind(node)} ${nodeLabel(node)} ${nodeSummary(node)} ${nodeId(node)} ${formatJson(nodeMetadata(node))}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
  const rankedNodes = rankNodesForCleanView(rawNodes, state.centralGraph.edges || [], query);
  const cap = renderCapForMode(query);
  const baseNodes = rankedNodes.slice(0, cap);
  const ids = new Set(baseNodes.map(nodeId));
  const baseEdges = rankEdgesForCleanView((state.centralGraph.edges || []).filter(edge => ids.has(edgeSource(edge)) && ids.has(edgeTarget(edge))));
  state.visibleNodes = baseNodes;
  state.visibleEdges = baseEdges;
  state.renderCap = cap;
  state.cappedOut = Math.max(0, rawNodes.length - baseNodes.length);
  state.layoutTicks = 0;
  ensurePositions(state, baseNodes);
  simulateLayout(state, baseNodes.length > 700 ? 8 : 54);
  if (state.selectedId && !ids.has(state.selectedId)) state.selectedId = "";
  renderInspector();
  renderLineage();
  renderLegend();
  updateStats();
}

function renderCapForMode(query) {
  if (query) return state.centralGraph.full ? 900 : 620;
  if (state.mode === "trace") return 720;
  if (state.mode === "similarity") return 360;
  if (state.mode === "causal") return 460;
  return state.centralGraph.full ? 760 : 420;
}

function rankNodesForCleanView(nodes, edges, query) {
  const degree = new Map();
  edges.forEach(edge => {
    degree.set(edgeSource(edge), (degree.get(edgeSource(edge)) || 0) + 1);
    degree.set(edgeTarget(edge), (degree.get(edgeTarget(edge)) || 0) + 1);
  });
  return [...nodes].sort((a, b) => scoreNode(b, degree, query) - scoreNode(a, degree, query));
}

function scoreNode(node, degree, query) {
  const kind = nodeKind(node);
  const status = nodeStatus(node);
  let score = degree.get(nodeId(node)) || 0;
  if (["Decision", "Problem", "Cause", "Fix", "ReasoningNode"].includes(kind)) score += 80;
  if (["Packet", "Commit", "GitCommit", "CodeNode", "CodeVersion", "Symbol"].includes(kind)) score += 46;
  if (["Topic", "Cluster"].includes(kind)) score += 24;
  if (["EvidenceRef", "RawEvidenceRef", "ToolResult", "Prompt"].includes(kind)) score -= state.showSupport ? 8 : 120;
  if (["committed", "session_final", "accepted", "active"].includes(status)) score += 34;
  if (node.scope === "central") score += 20;
  if (query) {
    const haystack = `${kind} ${nodeLabel(node)} ${nodeSummary(node)} ${nodeId(node)} ${formatJson(nodeMetadata(node))}`.toLowerCase();
    if (haystack.includes(query)) score += 160;
  }
  return score;
}

function rankEdgesForCleanView(edges) {
  return [...edges].sort((a, b) => scoreEdge(b) - scoreEdge(a)).slice(0, Math.min(edges.length, 1300));
}

function scoreEdge(edge) {
  const kind = edgeKind(edge);
  if (["CAUSES", "RESOLVES", "SUPPORTS_ANSWER", "EXPLAINS_CODE", "HAS_REASONING_NODE"].includes(kind)) return 90;
  if (["COMMITTED_AS", "EXTRACTED_FROM_COMMIT", "TOUCHES_CODE"].includes(kind)) return 62;
  if (["CREATED", "EXTRACTED_AS"].includes(kind)) return 38;
  return 10;
}

function updateStats() {
  const nodeCount = state.visibleNodes.length;
  const edgeCount = state.visibleEdges.length;
  const full = state.centralGraph.full ? "whole graph" : "slice";
  $("graphStats").innerHTML = [
    ["visible nodes", nodeCount],
    ["visible edges", edgeCount],
    ["loaded", full],
    ["hidden", state.cappedOut],
    ["mode", state.mode],
  ].map(([label, value]) => `<div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
  $("sliceBtn").classList.toggle("active", !state.centralGraph.full);
  $("wholeBtn").classList.toggle("active", !!state.centralGraph.full);
}

function setMode(mode) {
  state.mode = mode;
  state.traceNodeIds = new Set();
  state.traceStages = [];
  qsa("[data-mode]").forEach(button => button.classList.toggle("active", button.dataset.mode === mode));
  $("modeCaption").textContent = {
    atlas: "Brain map: high-signal reasoning, packets, code, and commits. Raw provenance stays hidden until requested.",
    causal: "Causal replay: evidence, reasoning, and code lanes without dumping every raw event.",
    similarity: "Similarity lens: only retrievable memory clusters and code/reasoning anchors.",
    trace: "Retrieval trace: run a query to animate candidates, graph expansion, reranking, and answer support.",
  }[mode] || "Graph mode";
  rebuildGraph();
}

async function runTrace() {
  const query = ($("traceQuery")?.value || $("graphFilter")?.value || "").trim();
  if (!query) return;
  setMode("trace");
  $("tracePanel").innerHTML = `<div class="trace-card"><strong>running retrieval</strong><span>${escapeHtml(query)}</span></div>`;
  setStatus("retrieving trace", "warn");
  try {
    const result = await apiPost("/graph/retrieve", {
      query,
      repo_id: state.repoId || "",
      limit: 10,
      use_vector: true,
      require_vector: false,
      include_answer: true,
    });
    buildTraceFromRetrieval(result, query);
    setStatus("retrieval trace ready", "good");
  } catch (error) {
    $("tracePanel").innerHTML = `<div class="trace-card error"><strong>trace failed</strong><span>${escapeHtml(error.message)}</span></div>`;
    setStatus("trace failed", "bad");
  }
}

function buildTraceFromRetrieval(result, query) {
  const retrieval = result.retrieval || {};
  const hits = retrieval.hits || [];
  const citations = result.answer?.citations || [];
  const existing = new Map(state.centralGraph.nodes.map(node => [nodeId(node), node]));
  const extraNodes = [];
  const extraEdges = [];
  const queryNode = { id: `query:${Date.now()}`, kind: "Query", label: query, summary: `Retrieval query: ${query}`, status: "active", scope: "trace" };
  const answerNode = { id: `answer:${Date.now()}`, kind: "Answer", label: "Indexed graph answer", summary: result.answer?.text || "No answer text returned.", status: "active", scope: "trace" };
  extraNodes.push(queryNode, answerNode);
  const hitIds = [];
  hits.forEach((hit, index) => {
    const graphId = hit.document?.graph_node_id || hit.graph_node?.id;
    if (graphId) hitIds.push(graphId);
    const node = graphId && existing.get(graphId);
    if (!node && graphId) {
      extraNodes.push({ id: graphId, kind: hit.document?.doc_type || "ReasoningNode", label: hit.document?.title || graphId, summary: hit.document?.body || "retrieval hit", status: "active", scope: "trace" });
    }
    if (graphId) {
      extraEdges.push({ id: `trace:q:${index}:${graphId}`, source_id: queryNode.id, target_id: graphId, kind: index < 3 ? "SUPPORTS_ANSWER" : "SIMILAR_TO" });
      extraEdges.push({ id: `trace:a:${index}:${graphId}`, source_id: graphId, target_id: answerNode.id, kind: "SUPPORTS_ANSWER" });
    }
    (hit.neighbors || []).slice(0, 4).forEach((neighbor, nIndex) => {
      if (!neighbor.id) return;
      if (!existing.has(neighbor.id)) extraNodes.push({ id: neighbor.id, kind: neighbor.kind || "Node", label: neighbor.label || neighbor.id, summary: neighbor.summary || "graph neighbor", status: "active", scope: "trace" });
      if (graphId) extraEdges.push({ id: `trace:n:${index}:${nIndex}:${neighbor.id}`, source_id: graphId, target_id: neighbor.id, kind: neighbor.edge_kind || "RELATED" });
    });
  });

  state.centralGraph = {
    ...state.centralGraph,
    nodes: dedupeNodes([...state.centralGraph.nodes, ...extraNodes]),
    edges: [...state.centralGraph.edges, ...extraEdges],
  };
  state.traceStages = [
    { name: "query", ids: [queryNode.id], caption: "Query enters the graph as an active node." },
    { name: "candidates", ids: hitIds.slice(0, 10), caption: `Candidates from ${Object.entries(retrieval.candidate_counts || {}).map(([k, v]) => `${k}:${v}`).join(" ") || "indexed retrieval"}.` },
    { name: "neighborhood", ids: hits.flatMap(hit => (hit.neighbors || []).map(n => n.id)).filter(Boolean).slice(0, 16), caption: "Graph neighborhood expansion adds adjacent evidence/code/commit nodes." },
    { name: "rerank", ids: hitIds.slice(0, 5), caption: `Reranker: ${retrieval.reranker || "deterministic"}.` },
    { name: "answer", ids: [answerNode.id, ...citations.map(c => c.graph_node_id).filter(Boolean).slice(0, 5)], caption: "Answer support path with citations." },
  ];
  state.traceStageIndex = 0;
  applyTraceStage(0);
  $("tracePanel").innerHTML = renderTraceResult(result, query);
  fillFilters();
  rebuildGraph();
  applyTraceStage(0);
}

function dedupeNodes(nodes) {
  const seen = new Set();
  return nodes.filter(node => {
    const id = nodeId(node);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function applyTraceStage(index) {
  if (!state.traceStages.length) return;
  state.traceStageIndex = Math.max(0, Math.min(state.traceStages.length - 1, index));
  const ids = new Set();
  state.traceStages.slice(0, state.traceStageIndex + 1).forEach(stage => stage.ids.forEach(id => ids.add(id)));
  state.traceNodeIds = ids;
  renderTraceSteps();
}

function renderTraceSteps() {
  const target = $("traceSteps");
  if (!target) return;
  target.innerHTML = state.traceStages.map((stage, index) => `<button class="trace-step ${index === state.traceStageIndex ? "active" : ""}" data-stage-index="${index}"><strong>${escapeHtml(stage.name)}</strong><span>${escapeHtml(stage.caption)}</span></button>`).join("");
}

function renderTraceResult(result, query) {
  const retrieval = result.retrieval || {};
  const hits = retrieval.hits || [];
  const answer = result.answer || {};
  return `<div class="trace-summary">
    <p class="eyebrow">Retrieval trace</p>
    <h3>${escapeHtml(retrieval.intent || "query")}</h3>
    <p>${escapeHtml(query)}</p>
    <div class="trace-pills">
      <span>vector ${escapeHtml(retrieval.vector_status || "unknown")}</span>
      <span>${escapeHtml(retrieval.reranker || "reranker")}</span>
      <span>${escapeHtml(hits.length)} hits</span>
    </div>
    <pre>${escapeHtml(answer.text || "No generated answer returned.")}</pre>
  </div>`;
}

function renderInspector() {
  const target = $("inspectorBody");
  const node = state.visibleNodes.find(item => nodeId(item) === state.selectedId) || state.centralGraph.nodes.find(item => nodeId(item) === state.selectedId);
  if (!node) {
    $("inspectorTitle").textContent = "Select a node";
    $("inspectorSubtitle").textContent = "Click any point to inspect provenance, lineage, and connected knowledge.";
    target.innerHTML = `<div class="empty-inspector">No node selected.</div>`;
    return;
  }
  const edges = state.centralGraph.edges.filter(edge => edgeSource(edge) === nodeId(node) || edgeTarget(edge) === nodeId(node));
  $("inspectorTitle").textContent = truncate(nodeLabel(node), 70);
  $("inspectorSubtitle").textContent = `${nodeKind(node)} | ${nodeStatus(node)} | ${node.scope || "session"}`;
  target.innerHTML = `
    <div class="node-identity ${graphClassForNode(node)}">
      <span>${escapeHtml(nodeKind(node))}</span>
      <strong>${escapeHtml(nodeId(node))}</strong>
    </div>
    <div class="inspector-grid">
      ${kv("summary", nodeSummary(node))}
      ${kv("session", node.session_id || "-")}
      ${kv("commit", node.commit_id || "-")}
      ${kv("evidence", node.evidence_id || "-")}
      ${kv("created", node.created_at || "-")}
    </div>
    <details open><summary>Edges (${edges.length})</summary><div class="edge-list">${edges.map(renderEdge).join("") || `<p class="muted">No connected edges.</p>`}</div></details>
    <details><summary>Metadata</summary><pre class="code-block">${escapeHtml(formatJson(nodeMetadata(node)))}</pre></details>
    <div class="button-row"><button id="focusBtn" class="ghost-btn">Focus neighborhood</button><button id="provenanceBtn" class="ghost-btn">Reveal provenance</button></div>`;
  $("focusBtn")?.addEventListener("click", focusNeighborhood);
  $("provenanceBtn")?.addEventListener("click", () => {
    state.showSupport = true;
    $("supportToggle").checked = true;
    rebuildGraph();
    selectNode(nodeId(node));
  });
}

function kv(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "-")}</strong></div>`;
}

function renderEdge(edge) {
  const direction = edgeSource(edge) === state.selectedId ? "out" : "in";
  const other = direction === "out" ? edgeTarget(edge) : edgeSource(edge);
  const style = styleForEdge(edge);
  return `<button class="edge-row" data-node-id="${escapeHtml(other)}" style="--edge-color:${style.color}"><strong>${escapeHtml(edgeKind(edge))}</strong><span>${escapeHtml(direction)} ${escapeHtml(other)}</span></button>`;
}

function renderLineage() {
  const target = $("lineageFlow");
  if (!state.selectedId) {
    target.innerHTML = `<span class="muted">Select a node to see evidence -> reasoning -> code/commit lineage.</span>`;
    return;
  }
  const byId = new Map(state.centralGraph.nodes.map(node => [nodeId(node), node]));
  const incoming = [];
  const outgoing = [];
  state.centralGraph.edges.forEach(edge => {
    if (edgeTarget(edge) === state.selectedId) incoming.push({ edge, node: byId.get(edgeSource(edge)) });
    if (edgeSource(edge) === state.selectedId) outgoing.push({ edge, node: byId.get(edgeTarget(edge)) });
  });
  const selected = byId.get(state.selectedId);
  const cards = [
    ...incoming.slice(0, 5).map(item => lineageCard(item.node, edgeKind(item.edge), "before")),
    lineageCard(selected, "selected", "active"),
    ...outgoing.slice(0, 5).map(item => lineageCard(item.node, edgeKind(item.edge), "after")),
  ];
  target.innerHTML = cards.join("");
}

function lineageCard(node, edge, phase) {
  if (!node) return `<div class="lineage-card ${phase}"><strong>Outside sample</strong><span>${escapeHtml(edge)}</span><p>Endpoint is hidden by the current filter or slice.</p></div>`;
  return `<button class="lineage-card ${phase}" data-node-id="${escapeHtml(nodeId(node))}"><strong>${escapeHtml(nodeKind(node))}</strong><span>${escapeHtml(edge)}</span><p>${escapeHtml(truncate(nodeSummary(node) || nodeLabel(node), 118))}</p></button>`;
}

function renderLegend() {
  const groups = [
    ["evidence", "raw / cleaned / delta"],
    ["reasoning", "problem / cause / decision / fix"],
    ["code", "commit / code / symbol"],
    ["retrieval", "query / answer trace"],
    ["memory", "topic / cluster / context"],
  ];
  $("legend").innerHTML = groups.map(([klass, label]) => `<div><span class="legend-dot ${klass}"></span><strong>${escapeHtml(label)}</strong></div>`).join("");
}

function selectNode(id) {
  state.selectedId = id || "";
  if (id) state.traceNodeIds.add(id);
  renderInspector();
  renderLineage();
}

function focusNeighborhood() {
  if (!state.selectedId) return;
  const ids = collectNeighbors(state, state.selectedId);
  const points = [...ids].map(id => state.positions.get(id)).filter(Boolean);
  if (!points.length) return;
  const projected = points.map(point => projectPoint(state, point)).filter(point => point.visible);
  if (!projected.length) return;
  const cx = projected.reduce((sum, point) => sum + point.x, 0) / projected.length;
  const cy = projected.reduce((sum, point) => sum + point.y, 0) / projected.length;
  const rect = state.canvas.getBoundingClientRect();
  state.scale = Math.max(1.05, Math.min(3.0, state.scale));
  state.tx += rect.width / 2 - cx;
  state.ty += rect.height / 2 - cy;
}

function bindEvents() {
  $("reloadBtn").addEventListener("click", () => loadGraph({ full: state.centralGraph.full }).catch(showFatal));
  $("repoScopeSelect")?.addEventListener("change", event => {
    state.repoId = event.target.value || "";
    state.selectedId = "";
    loadGraph({ full: state.centralGraph.full }).catch(showFatal);
  });
  $("sliceBtn").addEventListener("click", () => loadGraph({ full: false }).catch(showFatal));
  $("wholeBtn").addEventListener("click", () => loadGraph({ full: true }).catch(showFatal));
  $("traceBtn").addEventListener("click", runTrace);
  $("traceQuery").addEventListener("keydown", event => { if (event.key === "Enter") runTrace(); });
  $("graphFilter").addEventListener("input", rebuildGraph);
  $("kindFilter").addEventListener("change", rebuildGraph);
  $("statusFilter").addEventListener("change", rebuildGraph);
  $("supportToggle").addEventListener("change", event => { state.showSupport = event.target.checked; rebuildGraph(); });
  $("labelToggle").addEventListener("change", event => { state.showLabels = event.target.checked; });
  $("axesToggle").addEventListener("change", event => { state.showAxes = event.target.checked; });
  qsa("[data-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
  document.body.addEventListener("click", event => {
    const edge = event.target.closest(".edge-row, .lineage-card");
    if (edge?.dataset.nodeId) {
      selectNode(edge.dataset.nodeId);
      focusNeighborhood();
    }
    const stage = event.target.closest(".trace-step");
    if (stage) applyTraceStage(Number(stage.dataset.stageIndex || 0));
  });
  bindCanvasEvents();
  window.addEventListener("resize", () => resizeCanvas(state));
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", event => { if (event.code === "Space") state.spaceDown = false; });
}

function bindCanvasEvents() {
  const canvas = state.canvas;
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("contextmenu", event => event.preventDefault());
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointerleave", onPointerUp);
  canvas.addEventListener("dblclick", focusNeighborhood);
}

function onWheel(event) {
  event.preventDefault();
  const factor = event.deltaY > 0 ? 0.88 : 1.13;
  state.scale = Math.max(0.12, Math.min(7.5, state.scale * factor));
}

function onPointerDown(event) {
  const hit = nodeAtPoint(state, event.clientX, event.clientY);
  if (hit) selectNode(nodeId(hit));
  state.dragging = true;
  state.dragMode = event.shiftKey || state.spaceDown || event.button === 1 || event.button === 2 ? "pan" : "rotate";
  state.dragStart = {
    x: event.clientX,
    y: event.clientY,
    tx: state.tx,
    ty: state.ty,
    rotationX: state.rotationX,
    rotationY: state.rotationY,
  };
  state.canvas.classList.add("dragging");
  state.canvas.setPointerCapture(event.pointerId);
}

function onPointerMove(event) {
  if (state.dragging && state.dragStart) {
    const dx = event.clientX - state.dragStart.x;
    const dy = event.clientY - state.dragStart.y;
    if (state.dragMode === "pan") {
      state.tx = state.dragStart.tx + dx;
      state.ty = state.dragStart.ty + dy;
    } else {
      state.rotationY = state.dragStart.rotationY + dx * 0.006;
      state.rotationX = Math.max(-1.38, Math.min(1.38, state.dragStart.rotationX + dy * 0.005));
    }
    return;
  }
  const hit = nodeAtPoint(state, event.clientX, event.clientY);
  state.hoveredId = hit ? nodeId(hit) : "";
}

function onPointerUp(event) {
  state.dragging = false;
  state.dragStart = null;
  state.dragMode = "";
  state.canvas.classList.remove("dragging");
  try { state.canvas.releasePointerCapture(event.pointerId); } catch (_) {}
}

function onKeyDown(event) {
  if (event.code === "Space") {
    state.spaceDown = true;
    event.preventDefault();
    return;
  }
  const key = event.key.toLowerCase();
  const step = event.shiftKey ? 76 : 34;
  if (key === "arrowleft" || key === "a") state.tx += step;
  else if (key === "arrowright" || key === "d") state.tx -= step;
  else if (key === "arrowup" || key === "w") state.ty += step;
  else if (key === "arrowdown" || key === "s") state.ty -= step;
  else if (key === "+" || key === "=") state.scale = Math.min(7.5, state.scale * 1.12);
  else if (key === "-" || key === "_") state.scale = Math.max(0.12, state.scale * 0.88);
  else if (key === "0") resetCamera();
  else return;
  event.preventDefault();
}

function resetCamera() {
  state.tx = 0;
  state.ty = 0;
  state.scale = 1;
  state.rotationX = -0.48;
  state.rotationY = 0.72;
}

function animationLoop(now) {
  if (!state.running) return;
  const delta = now - (state.lastFrame || now);
  state.lastFrame = now;
  if (state.layoutTicks < 90 && state.visibleNodes.length <= 620) {
    simulateLayout(state, 1);
    state.layoutTicks += 1;
  }
  drawScene(state, now);
  requestAnimationFrame(animationLoop);
}

function showFatal(error) {
  console.error(error);
  setStatus(error.message || "graph failed", "bad");
  $("inspectorBody").innerHTML = `<div class="trace-card error"><strong>Graph failed</strong><span>${escapeHtml(error.stack || error.message)}</span></div>`;
}

async function init() {
  state.canvas = $("graphCanvas");
  state.ctx = state.canvas.getContext("2d");
  prepareContext(state.ctx);
  resizeCanvas(state);
  bindEvents();
  renderLegend();
  setMode("atlas");
  await loadRepos();
  await loadGraph({ full: false });
  state.running = true;
  requestAnimationFrame(animationLoop);
}

init().catch(showFatal);
