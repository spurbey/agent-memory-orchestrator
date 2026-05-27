import { truncate } from "../core/dom.js";
import { edgeKind, edgeSource, edgeTarget, nodeId, nodeKind, nodeLabel, nodeStatus, styleForEdge, styleForNode } from "./semantics.js";

export function resizeCanvas(state) {
  const canvas = state.canvas;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  state.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

export function rotatePoint(state, point) {
  const x = point.x || 0;
  const y = point.y || 0;
  const z = point.z || 0;
  const cy = Math.cos(state.rotationY);
  const sy = Math.sin(state.rotationY);
  const cx = Math.cos(state.rotationX);
  const sx = Math.sin(state.rotationX);
  const x1 = x * cy - z * sy;
  const z1 = x * sy + z * cy;
  const y2 = y * cx - z1 * sx;
  const z2 = y * sx + z1 * cx;
  return { x: x1, y: y2, z: z2 };
}

export function projectPoint(state, point) {
  const rect = state.canvas.getBoundingClientRect();
  const rotated = rotatePoint(state, point);
  const depth = Math.max(80, state.cameraDistance - rotated.z);
  const perspective = (state.fov / depth) * state.scale;
  return {
    x: rect.width / 2 + state.tx + rotated.x * perspective,
    y: rect.height / 2 + state.ty + rotated.y * perspective,
    z: rotated.z,
    depth,
    perspective,
    visible: depth > 30,
  };
}

export function drawScene(state, now = performance.now()) {
  const canvas = state.canvas;
  const ctx = state.ctx;
  if (!canvas || !ctx) return;
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  drawBackground(ctx, rect, state);
  state.screenPositions = new Map();
  state.visibleNodes.forEach(node => {
    const id = nodeId(node);
    const position = state.positions.get(id);
    if (!position) return;
    const projected = projectPoint(state, position);
    projected.radius = radiusForNode(node) * Math.max(0.72, Math.min(3.0, projected.perspective));
    state.screenPositions.set(id, projected);
  });

  const selectedNeighbors = collectNeighbors(state, state.selectedId);
  const selectedEdgeIds = collectEdgeIds(state, state.selectedId);
  drawEdges(ctx, state, selectedEdgeIds, now);
  drawNodes(ctx, state, selectedNeighbors);
  drawDepthAxes(ctx, rect, state);
}

function drawBackground(ctx, rect, state) {
  const bg = ctx.createRadialGradient(rect.width * 0.50, rect.height * 0.44, 0, rect.width * 0.50, rect.height * 0.44, Math.max(rect.width, rect.height) * 0.74);
  bg.addColorStop(0, state.mode === "trace" ? "#102019" : "#0d1813");
  bg.addColorStop(0.48, "#040907");
  bg.addColorStop(1, "#010302");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, rect.width, rect.height);
  drawBrainLobes(ctx, rect);
  ctx.save();
  ctx.globalAlpha = 0.18;
  ctx.strokeStyle = "#244139";
  ctx.lineWidth = 1;
  const gap = 72;
  const offset = 0;
  for (let x = -gap + offset; x < rect.width + gap; x += gap) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x - rect.height * 0.24, rect.height);
    ctx.stroke();
  }
  ctx.restore();
}

function drawBrainLobes(ctx, rect) {
  ctx.save();
  ctx.globalAlpha = 0.18;
  const left = ctx.createRadialGradient(rect.width * 0.42, rect.height * 0.48, 20, rect.width * 0.42, rect.height * 0.48, rect.width * 0.28);
  left.addColorStop(0, "rgba(128, 222, 198, 0.34)");
  left.addColorStop(0.56, "rgba(128, 222, 198, 0.08)");
  left.addColorStop(1, "rgba(128, 222, 198, 0)");
  ctx.fillStyle = left;
  ctx.beginPath();
  ctx.ellipse(rect.width * 0.42, rect.height * 0.48, rect.width * 0.25, rect.height * 0.31, -0.18, 0, Math.PI * 2);
  ctx.fill();
  const right = ctx.createRadialGradient(rect.width * 0.58, rect.height * 0.49, 20, rect.width * 0.58, rect.height * 0.49, rect.width * 0.28);
  right.addColorStop(0, "rgba(183, 245, 110, 0.26)");
  right.addColorStop(0.58, "rgba(183, 245, 110, 0.07)");
  right.addColorStop(1, "rgba(183, 245, 110, 0)");
  ctx.fillStyle = right;
  ctx.beginPath();
  ctx.ellipse(rect.width * 0.58, rect.height * 0.49, rect.width * 0.25, rect.height * 0.31, 0.18, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 0.22;
  ctx.strokeStyle = "rgba(242, 207, 120, 0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(rect.width * 0.48, rect.height * 0.25);
  ctx.bezierCurveTo(rect.width * 0.52, rect.height * 0.38, rect.width * 0.48, rect.height * 0.58, rect.width * 0.53, rect.height * 0.75);
  ctx.stroke();
  ctx.restore();
}

function drawDepthAxes(ctx, rect, state) {
  if (!state.showAxes) return;
  ctx.save();
  ctx.globalAlpha = 0.45;
  ctx.font = "11px ui-monospace, monospace";
  const origin = projectPoint(state, { x: -460, y: 300, z: -230 });
  const xEnd = projectPoint(state, { x: -330, y: 300, z: -230 });
  const yEnd = projectPoint(state, { x: -460, y: 170, z: -230 });
  const zEnd = projectPoint(state, { x: -460, y: 300, z: -100 });
  axis(ctx, origin, xEnd, "x", "#80dec6");
  axis(ctx, origin, yEnd, "y", "#f2cf78");
  axis(ctx, origin, zEnd, "z", "#bda2ff");
  ctx.restore();
}

function axis(ctx, from, to, label, color) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(to.x, to.y);
  ctx.stroke();
  ctx.fillText(label, to.x + 4, to.y + 4);
}

function drawEdges(ctx, state, selectedEdgeIds, now) {
  ctx.save();
  ctx.lineCap = "round";
  const edgeDrawList = state.visibleEdges
    .map(edge => ({ edge, source: state.screenPositions.get(edgeSource(edge)), target: state.screenPositions.get(edgeTarget(edge)) }))
    .filter(item => item.source?.visible && item.target?.visible)
    .sort((left, right) => ((left.source.z + left.target.z) / 2) - ((right.source.z + right.target.z) / 2));

  edgeDrawList.forEach(({ edge, source, target }) => {
    const style = styleForEdge(edge);
    const highlighted = selectedEdgeIds.has(String(edge.id)) || (state.selectedId && (edgeSource(edge) === state.selectedId || edgeTarget(edge) === state.selectedId));
    const traceActive = state.traceNodeIds.has(edgeSource(edge)) && state.traceNodeIds.has(edgeTarget(edge));
    const depthAlpha = Math.max(0.07, Math.min(0.62, 1.04 - ((source.depth + target.depth) / 2) / 1450));
    ctx.globalAlpha = state.selectedId ? (highlighted ? 0.92 : 0.10) : traceActive ? 0.92 : depthAlpha;
    ctx.strokeStyle = traceActive ? "#f2cf78" : style.color;
    ctx.lineWidth = (highlighted || traceActive ? 1.8 : 1) * Math.max(0.75, style.width * ((source.perspective + target.perspective) / 2));
    ctx.setLineDash(style.dash || []);
    drawCurvedLink(ctx, source, target, edgeKind(edge));
    if ((highlighted || traceActive) && state.showLabels && state.scale > 0.34) {
      ctx.globalAlpha = 0.88;
      ctx.fillStyle = traceActive ? "#fff3a3" : "#a7beb3";
      ctx.font = "11px ui-monospace, monospace";
      ctx.fillText(edgeKind(edge), (source.x + target.x) / 2 + 8, (source.y + target.y) / 2 - 7);
    }
  });
  ctx.setLineDash([]);
  ctx.restore();
}

function drawCurvedLink(ctx, source, target, kind) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.sqrt(dx * dx + dy * dy) || 1;
  const curve = kind === "RELATED" ? 0 : Math.min(70, Math.max(12, distance * 0.09));
  const normalX = -dy / distance;
  const normalY = dx / distance;
  const cx = (source.x + target.x) / 2 + normalX * curve;
  const cy = (source.y + target.y) / 2 + normalY * curve;
  ctx.beginPath();
  ctx.moveTo(source.x, source.y);
  ctx.quadraticCurveTo(cx, cy, target.x, target.y);
  ctx.stroke();
}

function drawNodes(ctx, state, selectedNeighbors) {
  const list = state.visibleNodes
    .map(node => ({ node, projected: state.screenPositions.get(nodeId(node)) }))
    .filter(item => item.projected?.visible)
    .sort((left, right) => left.projected.z - right.projected.z);

  list.forEach(({ node, projected }) => {
    const id = nodeId(node);
    const selected = id === state.selectedId;
    const neighbor = selectedNeighbors.has(id);
    const hovered = id === state.hoveredId;
    const traced = state.traceNodeIds.has(id);
    const style = styleForNode(node);
    const depthAlpha = Math.max(0.30, Math.min(1, 1.09 - projected.depth / 1720));
    const alpha = state.selectedId ? (selected || neighbor ? 1 : 0.16) : traced ? 1 : depthAlpha;
    const radius = projected.radius * (selected ? 1.7 : hovered ? 1.36 : traced ? 1.24 : 1);
    ctx.save();
    ctx.globalAlpha = alpha;
    if (selected || hovered || traced) drawNodeHalo(ctx, projected, radius, selected ? "#ffffff" : style.halo, selected ? 0.30 : 0.22);
    drawNodeShape(ctx, projected, radius, style, nodeStatus(node), selected || traced);
    const traceLabel = state.mode === "trace" && traced && state.visibleNodes.length <= 14;
    const atlasLabel = state.mode !== "trace" && (neighbor || traced || projected.perspective > 0.78 || state.visibleNodes.length < 54);
    const showLabel = state.showLabels && (selected || hovered || traceLabel || atlasLabel);
    if (showLabel) {
      ctx.globalAlpha = selected || hovered || traced ? 1 : Math.min(0.86, alpha + 0.18);
      ctx.font = `${selected ? 13 : 11}px ui-monospace, monospace`;
      ctx.fillStyle = selected || traced ? "#eff7ef" : "#c8d8d0";
      ctx.fillText(`${nodeKind(node)}: ${truncate(nodeLabel(node), selected ? 48 : 31)}`, projected.x + radius + 6, projected.y + 4);
    }
    ctx.restore();
  });
}

function drawNodeHalo(ctx, projected, radius, color, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(projected.x, projected.y, radius * 3.4, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawNodeShape(ctx, projected, radius, style, status, emphasized) {
  if (!ctx) return;
  ctx.fillStyle = style.color;
  ctx.strokeStyle = emphasized ? "#ffffff" : status === "draft" ? "rgba(255,255,255,0.36)" : "rgba(3,8,6,0.92)";
  ctx.lineWidth = emphasized ? 2.8 : 1.4;
  if (style.shape === "diamond") polygon(ctx, projected.x, projected.y, radius, 4, Math.PI / 4);
  else if (style.shape === "triangle") polygon(ctx, projected.x, projected.y, radius * 1.15, 3, -Math.PI / 2);
  else if (style.shape === "hex") polygon(ctx, projected.x, projected.y, radius, 6, Math.PI / 6);
  else if (style.shape === "square" || style.shape === "box") roundedBox(ctx, projected.x, projected.y, radius * 1.65, radius * 1.3);
  else if (style.shape === "file") fileShape(ctx, projected.x, projected.y, radius * 1.7, radius * 1.95);
  else {
    ctx.beginPath();
    ctx.arc(projected.x, projected.y, radius, 0, Math.PI * 2);
    ctx.fill();
    if (style.shape === "ring") {
      ctx.globalAlpha *= 0.95;
      ctx.beginPath();
      ctx.arc(projected.x, projected.y, radius * 0.52, 0, Math.PI * 2);
      ctx.fillStyle = "#06100c";
      ctx.fill();
      ctx.fillStyle = style.color;
    }
  }
  ctx.stroke();
}

function polygon(ctx, x, y, radius, sides, rotate = 0) {
  ctx.beginPath();
  for (let i = 0; i < sides; i += 1) {
    const angle = rotate + (i / sides) * Math.PI * 2;
    const px = x + Math.cos(angle) * radius;
    const py = y + Math.sin(angle) * radius;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fill();
}

function roundedBox(ctx, x, y, width, height) {
  const rx = x - width / 2;
  const ry = y - height / 2;
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") ctx.roundRect(rx, ry, width, height, Math.min(5, width * 0.2));
  else ctx.rect(rx, ry, width, height);
  ctx.fill();
}

function fileShape(ctx, x, y, width, height) {
  const left = x - width / 2;
  const top = y - height / 2;
  const fold = Math.min(5, width * 0.28);
  ctx.beginPath();
  ctx.moveTo(left, top);
  ctx.lineTo(left + width - fold, top);
  ctx.lineTo(left + width, top + fold);
  ctx.lineTo(left + width, top + height);
  ctx.lineTo(left, top + height);
  ctx.closePath();
  ctx.fill();
}

function radiusForNode(node) {
  return styleForNode(node).radius;
}

export function nodeAtPoint(state, clientX, clientY) {
  const rect = state.canvas.getBoundingClientRect();
  const point = { x: clientX - rect.left, y: clientY - rect.top };
  let best = null;
  let bestDistance = Infinity;
  state.visibleNodes.forEach(node => {
    const projected = state.screenPositions.get(nodeId(node));
    if (!projected?.visible) return;
    const radius = projected.radius + 9;
    const dx = point.x - projected.x;
    const dy = point.y - projected.y;
    const distance = Math.sqrt(dx * dx + dy * dy);
    if (distance < radius && distance < bestDistance) {
      best = node;
      bestDistance = distance;
    }
  });
  return best;
}

export function collectNeighbors(state, id) {
  const ids = new Set();
  if (!id) return ids;
  ids.add(id);
  state.visibleEdges.forEach(edge => {
    if (edgeSource(edge) === id) ids.add(edgeTarget(edge));
    if (edgeTarget(edge) === id) ids.add(edgeSource(edge));
  });
  return ids;
}

export function collectEdgeIds(state, id) {
  const ids = new Set();
  if (!id) return ids;
  state.visibleEdges.forEach(edge => {
    if (edgeSource(edge) === id || edgeTarget(edge) === id) ids.add(String(edge.id));
  });
  return ids;
}

export function prepareContext(ctx) {
  window.__amoGraphCtx = ctx;
}
