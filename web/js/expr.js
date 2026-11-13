// Expression compiler for display dynamics.
//
//   {{Tank1.Level}} * 2            tag reference (any direct address works inside {{ }})
//   {{modbus:PLC1/40001:F}} > 50
//   iif({{mem:Run}}, "RUN", "STOP")
//   <#Pump#>.Speed                 alias, resolved before compilation
//
// Expressions are compiled ONCE to a JS Function taking the value array; the
// result is cached by source text, so 1000 objects using the same expression
// share one function.
import { formatValue, truthy } from "./util.js";

const PLACE = /\{\{(.+?)\}\}/g;
const ALIAS = /<#([^#<>]+)#>/g;
const cache = new Map();

export const helpers = {
  iif: (c, a, b) => (truthy(c) ? a : b),
  bit: (v, n) => (Number(v) >> n) & 1,
  min: Math.min, max: Math.max, abs: Math.abs, round: (v, d = 0) => { const f = 10 ** d; return Math.round(v * f) / f; },
  floor: Math.floor, ceil: Math.ceil, sqrt: Math.sqrt, pow: Math.pow, sin: Math.sin, cos: Math.cos, PI: Math.PI,
  clamp: (v, a, b) => Math.max(a, Math.min(b, v)),
  scale: (v, a, b, c, d) => c + ((v - a) * (d - c)) / (b - a || 1),
  fmt: (v, f) => formatValue(v, f),
  str: (v) => (v == null ? "" : String(v)),
  num: (v) => Number(v),
  now: () => Date.now(),
  bool: (v) => truthy(v),
  len: (v) => (v == null ? 0 : String(v).length),
  pad: (v, n, c = "0") => String(v).padStart(n, c),
};
const HELPER_NAMES = Object.keys(helpers);
const HELPER_VALUES = Object.values(helpers);

export function resolveAliases(text, scopes) {
  if (typeof text !== "string" || !text.includes("<#")) return text;
  for (let i = 0; i < 8; i++) {
    let changed = false;
    text = text.replace(ALIAS, (m, name) => {
      for (const s of scopes) if (s && Object.prototype.hasOwnProperty.call(s, name)) { changed = true; return String(s[name]); }
      return m;
    });
    if (!changed) break;
  }
  return text;
}

/** Compile an expression -> { refs: [ref...], fn(values) , constant } */
export function compile(src) {
  src = String(src ?? "").trim();
  let c = cache.get(src);
  if (c) return c;
  const refs = [];
  let body = src.replace(PLACE, (_, ref) => {
    ref = ref.trim();
    let i = refs.indexOf(ref);
    if (i < 0) { i = refs.length; refs.push(ref); }
    return `_v[${i}]`;
  });
  if (!body) body = "undefined";
  let fn;
  try {
    // eslint-disable-next-line no-new-func
    const f = new Function("_v", ...HELPER_NAMES, `"use strict"; return (${body});`);
    fn = (vals) => f(vals, ...HELPER_VALUES);
  } catch (e) {
    const msg = `expression error: ${e.message} in ${src}`;
    fn = () => { throw new Error(msg); };
    fn.error = msg;
  }
  c = { src, refs, fn, constant: refs.length === 0 };
  cache.set(src, c);
  return c;
}

/** Normalise a dynamic definition into an expression string. */
export function exprOf(def) {
  if (def == null) return "";
  if (typeof def === "string") return def;
  if (def.expr != null && def.expr !== "") return String(def.expr);
  if (def.tag) return `{{${def.tag}}}`;
  return "";
}

/** List every tag reference used by an object tree (for cross reference / find & replace). */
export function refsInObject(o, out = new Set()) {
  const visit = (v) => {
    if (typeof v === "string") { for (const m of v.matchAll(PLACE)) out.add(m[1].trim()); }
    else if (Array.isArray(v)) v.forEach(visit);
    else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) { if (k === "tag" && typeof x === "string") out.add(x); else visit(x); }
  };
  visit(o);
  return out;
}
