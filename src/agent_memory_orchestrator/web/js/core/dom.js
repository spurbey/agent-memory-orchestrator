export function $(id) {
  return document.getElementById(id);
}

export function qsa(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

export function text(value, fallback = "") {
  return String(value ?? fallback);
}

export function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

export function truncate(value, length = 96) {
  const raw = text(value).replace(/\s+/g, " ").trim();
  if (raw.length <= length) return raw;
  return `${raw.slice(0, Math.max(0, length - 1))}...`;
}

export function formatJson(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

export function cssVar(name, fallback = "") {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
