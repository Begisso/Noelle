// Small shared helpers (no dependencies).
export const SVGNS = "http://www.w3.org/2000/svg";

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

export function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  return e;
}

export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const uid = (p = "o") => p + Math.random().toString(36).slice(2, 8);
export const deep = (o) => (o === undefined ? undefined : JSON.parse(JSON.stringify(o)));

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------------------------------------------------------------- number format
// Formats: "0", "0.00", "#,##0.0", "%d", "%.2f", "0.0 'bar'", "hex", "bin", "time", "date", "datetime"
const fmtCache = new Map();
export function formatValue(v, fmt) {
  if (v === null || v === undefined) return "";
  if (!fmt) {
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
    if (typeof v === "boolean") return v ? "1" : "0";
    return String(v);
  }
  let f = fmtCache.get(fmt);
  if (!f) { f = compileFormat(fmt); fmtCache.set(fmt, f); }
  return f(v);
}

function compileFormat(fmt) {
  if (fmt === "hex") return (v) => "0x" + (Number(v) >>> 0).toString(16).toUpperCase();
  if (fmt === "bin") return (v) => (Number(v) >>> 0).toString(2);
  if (fmt === "time") return (v) => new Date(toMs(v)).toLocaleTimeString();
  if (fmt === "date") return (v) => new Date(toMs(v)).toLocaleDateString();
  if (fmt === "datetime") return (v) => new Date(toMs(v)).toLocaleString();
  if (fmt.startsWith("onoff")) return (v) => (truthy(v) ? "ON" : "OFF");
  let m = fmt.match(/^%(\.(\d+))?([dfxXeg])$/);
  if (m) {
    const p = m[2] ? +m[2] : (m[3] === "d" ? 0 : 6), t = m[3];
    return (v) => {
      const n = Number(v);
      if (!isFinite(n)) return String(v);
      if (t === "d") return String(Math.round(n));
      if (t === "x") return Math.round(n).toString(16);
      if (t === "X") return Math.round(n).toString(16).toUpperCase();
      if (t === "e") return n.toExponential(p);
      if (t === "g") return String(+n.toPrecision(p || 6));
      return n.toFixed(p);
    };
  }
  // picture format with optional quoted suffix/prefix: 0.00 'bar'
  m = fmt.match(/^(.*?)([#0,]*0(?:\.(0+|#+))?)(.*)$/);
  if (m) {
    const pre = m[1].replace(/'/g, ""), suf = m[4].replace(/'/g, "");
    const dec = m[3] ? m[3].length : 0, group = m[2].includes(",");
    const nf = new Intl.NumberFormat(undefined, { minimumFractionDigits: m[3] && m[3][0] === "0" ? dec : 0, maximumFractionDigits: dec, useGrouping: group });
    return (v) => { const n = Number(v); return isFinite(n) && v !== "" ? pre + nf.format(n) + suf : String(v); };
  }
  return (v) => String(v);
}

const toMs = (v) => (typeof v === "number" && v < 1e11 ? v * 1000 : v);
export const truthy = (v) => !(v === false || v === 0 || v === "0" || v === "" || v == null || v === "false" || v === "False");

// ---------------------------------------------------------------- colors
export function parseColor(c) {
  if (!c) return [0, 0, 0, 1];
  if (c[0] === "#") {
    let h = c.slice(1);
    if (h.length === 3 || h.length === 4) h = [...h].map((x) => x + x).join("");
    const n = parseInt(h.slice(0, 6), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1];
  }
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (m) { const p = m[1].split(",").map((x) => parseFloat(x)); return [p[0], p[1], p[2], p[3] ?? 1]; }
  return [128, 128, 128, 1];
}
export function mixColor(a, b, t) {
  const A = parseColor(a), B = parseColor(b);
  const r = A.map((x, i) => x + (B[i] - x) * clamp(t, 0, 1));
  return `rgba(${r[0] | 0},${r[1] | 0},${r[2] | 0},${+r[3].toFixed(3)})`;
}

// ---------------------------------------------------------------- dialogs / toasts
let toastBox;
export function toast(msg, level = "info", ms = 3500) {
  if (!toastBox) { toastBox = el("div", { class: "toasts" }); document.body.append(toastBox); }
  const t = el("div", { class: "toast " + level }, msg);
  toastBox.append(t);
  setTimeout(() => t.classList.add("hide"), ms);
  setTimeout(() => t.remove(), ms + 400);
}

export function modal({ title, body, buttons = [["OK", true]], width = 420, onOpen } = {}) {
  return new Promise((resolve) => {
    const back = el("div", { class: "modal-back" });
    const box = el("div", { class: "modal", style: { width: width + "px" } });
    const close = (v) => { back.remove(); document.removeEventListener("keydown", key, true); resolve(v); };
    const key = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(null); }
      if (e.key === "Enter" && !(e.target.tagName === "TEXTAREA")) {
        const def = buttons.find((b) => b[2] === "primary") || buttons[0];
        e.stopPropagation(); e.preventDefault(); close(typeof def[1] === "function" ? def[1]() : def[1]);
      }
    };
    box.append(el("div", { class: "modal-title" }, title || ""), el("div", { class: "modal-body" }, body || ""),
      el("div", { class: "modal-buttons" }, buttons.map(([label, val, cls]) =>
        el("button", { class: cls || "", onclick: () => close(typeof val === "function" ? val() : val) }, label))));
    back.append(box);
    document.body.append(back);
    document.addEventListener("keydown", key, true);
    onOpen && onOpen(box);
    const first = box.querySelector("input,select,textarea");
    if (first) setTimeout(() => { first.focus(); first.select && first.select(); }, 30);
  });
}

export async function prompt(title, value = "", { label = "", type = "text" } = {}) {
  const inp = el("input", { type, value });
  const res = await modal({ title, body: el("div", {}, label ? el("label", {}, label) : "", inp),
    buttons: [["OK", () => inp.value, "primary"], ["Cancel", null]] });
  return res;
}

export async function confirmBox(text, title = "Confirm") {
  return !!(await modal({ title, body: text, buttons: [["Yes", true, "primary"], ["No", false]] }));
}

export function download(name, text, type = "text/plain") {
  const a = el("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

export function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
