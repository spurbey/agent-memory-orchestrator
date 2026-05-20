import { edgeKind, edgeSource, edgeTarget, graphClassForNode, nodeId, styleForEdge, styleForNode, VERSION_EDGE_TYPES } from "./semantics.js";

export function hashCode(value) {
  const text = String(value || "");
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function ensurePositions(state, nodes) {
  const count = Math.max(1, nodes.length);
  nodes.forEach((node, index) => {
    const id = nodeId(node);
    const hash = hashCode(id);
    const klass = graphClassForNode(node);
    const center = brainCenter(klass, state.mode);
    if (!state.positions.has(id)) {
      const theta = ((hash % 10000) / 10000) * Math.PI * 2;
      const phi = Math.acos(1 - (2 * ((index % count) + 0.5)) / count);
      const localShell = 48 + Math.cbrt(index + 1) * 14 + ((hash >>> 9) % 42);
      state.positions.set(id, {
        x: center.x + Math.sin(phi) * Math.cos(theta) * localShell,
        y: center.y + Math.sin(phi) * Math.sin(theta) * localShell * 0.72,
        z: center.z + Math.cos(phi) * localShell * 0.56 + ((hash >>> 17) % 56) - 28,
      });
      state.velocities.set(id, { x: 0, y: 0, z: 0 });
    }
  });
}

function brainCenter(klass, mode = "atlas") {
  if (mode === "causal") {
    return {
      evidence: { x: -430, y: 80, z: -40 },
      reasoning: { x: -110, y: -10, z: 70 },
      code: { x: 260, y: 72, z: -20 },
      retrieval: { x: 38, y: -230, z: 105 },
      memory: { x: -10, y: 138, z: -115 },
    }[klass] || { x: 0, y: 120, z: -120 };
  }
  if (mode === "similarity") {
    return {
      reasoning: { x: -170, y: -12, z: 90 },
      code: { x: 190, y: 35, z: 28 },
      retrieval: { x: 0, y: -180, z: 130 },
      memory: { x: 0, y: 95, z: -90 },
      evidence: { x: -310, y: 95, z: -95 },
    }[klass] || { x: 0, y: 85, z: -70 };
  }
  return {
    evidence: { x: -390, y: 88, z: -82 },
    reasoning: { x: -85, y: -34, z: 92 },
    code: { x: 285, y: 82, z: -36 },
    retrieval: { x: 18, y: -230, z: 124 },
    memory: { x: 0, y: 140, z: -132 },
  }[klass] || { x: 0, y: 118, z: -110 };
}

export function simulateLayout(state, iterations = 1) {
  const nodes = state.visibleNodes;
  const edges = state.visibleEdges;
  const idToNode = new Map(nodes.map(node => [nodeId(node), node]));
  const pairwiseRepulsion = nodes.length <= 1000;
  for (let step = 0; step < iterations; step += 1) {
    if (pairwiseRepulsion) applyRepulsion(state, nodes);
    applyLinks(state, edges, idToNode);
    applySemanticGravity(state, nodes);
  }
}

function applyRepulsion(state, nodes) {
  for (let i = 0; i < nodes.length; i += 1) {
    const a = nodes[i];
    const pa = state.positions.get(nodeId(a));
    const va = state.velocities.get(nodeId(a));
    if (!pa || !va) continue;
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = nodes[j];
      const pb = state.positions.get(nodeId(b));
      const vb = state.velocities.get(nodeId(b));
      if (!pb || !vb) continue;
      let dx = pa.x - pb.x;
      let dy = pa.y - pb.y;
      let dz = pa.z - pb.z;
      const dist2 = dx * dx + dy * dy + dz * dz + 180;
      const force = Math.min(2.9, 1750 / dist2);
      const dist = Math.sqrt(dist2);
      dx /= dist;
      dy /= dist;
      dz /= dist;
      va.x += dx * force;
      va.y += dy * force;
      va.z += dz * force;
      vb.x -= dx * force;
      vb.y -= dy * force;
      vb.z -= dz * force;
    }
  }
}

function applyLinks(state, edges, idToNode) {
  edges.forEach(edge => {
    const source = edgeSource(edge);
    const target = edgeTarget(edge);
    if (!idToNode.has(source) || !idToNode.has(target)) return;
    const ps = state.positions.get(source);
    const pt = state.positions.get(target);
    const vs = state.velocities.get(source);
    const vt = state.velocities.get(target);
    if (!ps || !pt || !vs || !vt) return;
    const dx = pt.x - ps.x;
    const dy = pt.y - ps.y;
    const dz = pt.z - ps.z;
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    const style = styleForEdge(edge);
    const desired = VERSION_EDGE_TYPES.has(edgeKind(edge)) ? 150 : style.particles ? 182 : 230;
    const force = (dist - desired) * 0.012;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    const fz = (dz / dist) * force;
    vs.x += fx;
    vs.y += fy;
    vs.z += fz;
    vt.x -= fx;
    vt.y -= fy;
    vt.z -= fz;
  });
}

function applySemanticGravity(state, nodes) {
  nodes.forEach(node => {
    const id = nodeId(node);
    const position = state.positions.get(id);
    const velocity = state.velocities.get(id);
    if (!position || !velocity) return;
    const style = styleForNode(node);
    const center = brainCenter(graphClassForNode(node), state.mode);
    velocity.x += (center.x - position.x) * 0.0022;
    velocity.y += (center.y - position.y) * 0.0022;
    velocity.z += (center.z - position.z) * 0.0018;
    velocity.x *= 0.84;
    velocity.y *= 0.84;
    velocity.z *= 0.84;
    const clamp = style.tier >= 6 ? 16 : 12;
    position.x += Math.max(-clamp, Math.min(clamp, velocity.x));
    position.y += Math.max(-clamp, Math.min(clamp, velocity.y));
    position.z += Math.max(-clamp, Math.min(clamp, velocity.z));
  });
}
