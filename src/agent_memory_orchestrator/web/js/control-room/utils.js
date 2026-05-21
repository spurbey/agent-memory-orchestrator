export { $, qsa, escapeHtml, formatJson, text, truncate } from "../core/dom.js";

export function timeAgo(iso) {
  if (!iso) return "unknown";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const diff = Math.max(0, Date.now() - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return new Date(iso).toLocaleString();
}

export function empty(message) {
  return `<div class="empty-state"><div><h2>No data</h2><p>${escapeHtml(message)}</p></div></div>`;
}

export function nodeId(node) {
  return text(node?.id || node?.node_id);
}

export function nodeKind(node) {
  return text(node?.kind || node?.type || "Node");
}

export function nodeStatus(node) {
  return text(node?.status || "draft");
}

export function nodeSummary(node) {
  return text(node?.summary || node?.label || "");
}

export function metadata(node) {
  return node && typeof node.metadata === "object" && node.metadata ? node.metadata : {};
}

export function edgeKind(edge) {
  return text(edge?.kind || edge?.relation || edge?.label || "RELATED");
}

export function readableKind(kind) {
  return text(kind).replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function statusTone(status) {
  const value = text(status).toLowerCase();
  if (["complete", "ready", "active", "accepted", "committed", "ok"].includes(value)) return "good";
  if (["failed", "error", "bad"].includes(value)) return "bad";
  if (["pending_model", "pending", "waiting", "draft"].includes(value)) return "warn";
  return "blue";
}

export function fileName(path) {
  const raw = text(path);
  return raw.split(/[\\/]/).filter(Boolean).pop() || raw;
}
