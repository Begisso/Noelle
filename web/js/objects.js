// Object type registry: defaults, property schema (for the inspector) and SVG rendering.
import { svg, el, formatValue } from "./util.js";

// property schema kinds: num, text, color, bool, select:a|b, expr, tag, font, display, image, pens, histpens, json, area
const COMMON_SHAPE = [["fill", "Fill", "color"], ["fillImage", "Fill texture (image)", "image"], ["invisible", "Invisible at runtime", "bool"], ["stroke", "Line color", "color"], ["sw", "Line width", "num"], ["dash", "Dash (e.g. 6,3)", "text"], ["opacity", "Opacity", "num"], ["shadow", "Shadow", "bool"]];
const TEXT_PROPS = [["text", "Text", "area"], ["stretch", "Stretch to box", "bool"], ["fs", "Font size", "num"], ["font", "Font family", "font"], ["fw", "Bold", "bool"], ["italic", "Italic", "bool"], ["color", "Text color", "color"], ["align", "Align", "select:left|center|right"], ["valign", "V-align", "select:top|middle|bottom"]];

export const TYPES = {
  rect: { label: "Rectangle", icon: "▭", defaults: { w: 120, h: 70, fill: "#5b6573", stroke: "#202428", sw: 1, rx: 0 }, props: [...COMMON_SHAPE, ["rx", "Corner radius", "num"], ["gradient", "Gradient (angle,c1,c2)", "text"]] },
  ellipse: { label: "Ellipse", icon: "◯", defaults: { w: 80, h: 80, fill: "#5b6573", stroke: "#202428", sw: 1 }, props: [...COMMON_SHAPE, ["gradient", "Gradient (angle,c1,c2)", "text"]] },
  line: { label: "Line", icon: "╱", poly: true, defaults: { points: [[0, 0], [120, 0]], stroke: "#d0d4da", sw: 2, fill: "none" }, props: [["stroke", "Line color", "color"], ["sw", "Line width", "num"], ["dash", "Dash", "text"], ["arrow", "Arrow", "select:none|end|start|both"], ["opacity", "Opacity", "num"]] },
  polyline: { label: "Polyline", icon: "⌇", poly: true, defaults: { points: [[0, 40], [40, 0], [80, 40], [120, 0]], stroke: "#d0d4da", sw: 2, fill: "none" }, props: [["stroke", "Line color", "color"], ["sw", "Line width", "num"], ["dash", "Dash", "text"], ["fill", "Fill", "color"], ["opacity", "Opacity", "num"]] },
  polygon: { label: "Polygon", icon: "⬠", poly: true, defaults: { points: [[0, 60], [50, 0], [100, 60]], stroke: "#202428", sw: 1, fill: "#5b6573" }, props: [...COMMON_SHAPE] },
  pipe: { label: "Pipe", icon: "═", poly: true, defaults: { points: [[0, 0], [160, 0]], stroke: "#8a94a3", sw: 12, fill: "none", flow: "" }, props: [["stroke", "Pipe color", "color"], ["sw", "Diameter", "num"], ["flow", "Flow when (expr)", "expr"], ["flowColor", "Flow color", "color"], ["opacity", "Opacity", "num"]] },
  arc: { label: "Arc / Pie", icon: "◔", defaults: { w: 100, h: 100, start: 0, end: 270, arcType: "pie", fill: "#5b6573", stroke: "#202428", sw: 1 }, props: [...COMMON_SHAPE, ["start", "Start angle", "num"], ["end", "End angle", "num"], ["arcType", "Type", "select:arc|pie|chord"]] },
  text: { label: "Text", icon: "T", defaults: { w: 140, h: 30, text: "Text", fs: 16, color: "#e6e9ee", align: "left", valign: "middle", fill: "none" }, props: [...TEXT_PROPS, ["fill", "Background", "color"], ["stroke", "Border", "color"]] },
  value: { label: "Process value", icon: "#", defaults: { w: 110, h: 30, expr: "{{sim:Sine}}", fmt: "0.0", units: "", fs: 16, color: "#7ef0a0", fill: "#14171b", stroke: "#3a414b", align: "right", valign: "middle", font: "Consolas" }, props: [["expr", "Value (tag/expr)", "expr"], ["fmt", "Format", "text"], ["units", "Units", "text"], ...TEXT_PROPS.filter((p) => p[0] !== "text"), ["fill", "Background", "color"], ["stroke", "Border", "color"], ["rx", "Corner radius", "num"]] },
  input: { label: "Data entry", icon: "⌨", defaults: { w: 110, h: 30, tag: "mem:Setpoint", fmt: "0.0", fs: 16, color: "#ffffff", fill: "#1d3046", stroke: "#4b7bb8", align: "right", valign: "middle", min: "", max: "", confirm: false, font: "Consolas" }, props: [["tag", "Tag", "tag"], ["fmt", "Format", "text"], ["units", "Units", "text"], ["min", "Min", "num"], ["max", "Max", "num"], ["confirm", "Confirm write", "bool"], ...TEXT_PROPS.filter((p) => p[0] !== "text"), ["fill", "Background", "color"], ["stroke", "Border", "color"], ["rx", "Corner radius", "num"]] },
  button: { label: "Button", icon: "⏺", defaults: { w: 120, h: 40, text: "Button", fs: 15, color: "#ffffff", fill: "#3d6fb6", stroke: "#25446f", rx: 4, align: "center", valign: "middle", style: "3d" }, props: [...TEXT_PROPS, ["fill", "Face color", "color"], ["stroke", "Border", "color"], ["rx", "Corner radius", "num"], ["style", "Style", "select:3d|flat|outline"]] },
  image: { label: "Image", icon: "🖼", defaults: { w: 120, h: 90, src: "", fit: "contain" }, props: [["src", "Image", "image"], ["fit", "Fit", "select:contain|cover|stretch"], ["opacity", "Opacity", "num"]] },
  bar: { label: "Bar graph", icon: "▮", defaults: { w: 40, h: 150, expr: "{{sim:Sine}}", min: 0, max: 100, dir: "up", barColor: "#3fa7ff", fill: "#14171b", stroke: "#3a414b", ticks: true }, props: [["expr", "Value (tag/expr)", "expr"], ["min", "Min", "num"], ["max", "Max", "num"], ["dir", "Direction", "select:up|down|left|right"], ["barColor", "Bar color", "color"], ["fill", "Background", "color"], ["stroke", "Border", "color"], ["ticks", "Scale ticks", "bool"]] },
  led: { label: "Indicator", icon: "●", defaults: { w: 28, h: 28, expr: "{{sim:Bool(4)}}", on: "#2ee66b", off: "#3a3f46", stroke: "#111", sw: 1, shape: "circle", blinkOn: false }, props: [["expr", "On when (tag/expr)", "expr"], ["on", "On color", "color"], ["off", "Off color", "color"], ["shape", "Shape", "select:circle|square"], ["blinkOn", "Blink when on", "bool"], ["stroke", "Border", "color"], ["sw", "Border width", "num"]] },
  gauge: { label: "Gauge", icon: "◷", defaults: { w: 160, h: 160, expr: "{{sim:Sine}}", min: 0, max: 100, fmt: "0.0", units: "", ranges: "70,90,#f0b429;90,100,#e5484d", fill: "#1b1f24", stroke: "#3a414b", color: "#e6e9ee", needle: "#ff5a36" }, props: [["expr", "Value (tag/expr)", "expr"], ["min", "Min", "num"], ["max", "Max", "num"], ["fmt", "Format", "text"], ["units", "Units", "text"], ["ranges", "Ranges (from,to,color;...)", "text"], ["fill", "Face", "color"], ["stroke", "Border", "color"], ["color", "Text color", "color"], ["needle", "Needle color", "color"]] },
  slider: { label: "Slider", icon: "⎯", defaults: { w: 200, h: 36, tag: "mem:Slider", min: 0, max: 100, step: 1, dir: "horizontal", fill: "#2a3038", barColor: "#3fa7ff" }, props: [["tag", "Tag", "tag"], ["min", "Min", "num"], ["max", "Max", "num"], ["step", "Step", "num"], ["dir", "Direction", "select:horizontal|vertical"], ["fill", "Track", "color"], ["barColor", "Fill", "color"]] },
  toggle: { label: "Toggle switch", icon: "⇋", defaults: { w: 70, h: 32, tag: "mem:Switch", on: "#2ee66b", off: "#59616c", onValue: 1, offValue: 0 }, props: [["tag", "Tag", "tag"], ["on", "On color", "color"], ["off", "Off color", "color"], ["onValue", "On value", "text"], ["offValue", "Off value", "text"], ["confirm", "Confirm write", "bool"]] },
  trend: { label: "Real-time trend", icon: "📈", defaults: { w: 420, h: 220, span: 300, pens: [{ expr: "{{sim:Sine(0,100,60)}}", color: "#3fa7ff", name: "Sine" }], ymin: "", ymax: "", fill: "#111418", grid: "#2a3038", color: "#aab2bd", legend: true }, props: [["pens", "Pens", "pens"], ["span", "Time span (s)", "num"], ["ymin", "Y min (auto)", "num"], ["ymax", "Y max (auto)", "num"], ["fill", "Background", "color"], ["grid", "Grid color", "color"], ["color", "Text color", "color"], ["legend", "Legend", "bool"]] },
  histtrend: { label: "Historian trend", icon: "🕓", defaults: { w: 520, h: 260, span: 3600, pens: [], live: true, fill: "#111418", grid: "#2a3038", color: "#aab2bd", legend: true }, props: [["pens", "Historian pens", "histpens"], ["span", "Time span (s)", "num"], ["live", "Follow now", "bool"], ["ymin", "Y min (auto)", "num"], ["ymax", "Y max (auto)", "num"], ["fill", "Background", "color"], ["grid", "Grid color", "color"], ["color", "Text color", "color"], ["legend", "Legend", "bool"]] },
  alarms: { label: "Alarm viewer", icon: "🔔", defaults: { w: 600, h: 200, fill: "#14171b", color: "#dfe3e8", fs: 13, filter: "" }, props: [["filter", "Filter (text/group)", "text"], ["fs", "Font size", "num"], ["fill", "Background", "color"], ["color", "Text color", "color"]] },
  group: { label: "Group", icon: "⧉", container: true, defaults: { children: [] }, props: [["opacity", "Opacity", "num"]] },
  symbol: { label: "Symbol", icon: "◈", defaults: { w: 100, h: 100, src: "", aliases: {} }, props: [["src", "Symbol file", "symbol"], ["aliases", "Alias values", "aliases"], ["opacity", "Opacity", "num"]] },
};

export const TOOLBOX = [
  ["Basic", ["rect", "ellipse", "line", "polyline", "polygon", "arc", "pipe", "text", "image"]],
  ["Process", ["value", "input", "button", "bar", "led", "gauge", "slider", "toggle"]],
  ["Data", ["trend", "histtrend", "alarms"]],
];

// ---------------------------------------------------------------- geometry
export function bbox(o) {
  if (o.points && o.points.length) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of o.points) { if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y; }
    return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
  }
  if (o.type === "group" && o.children) {
    if (!o.children.length) return { x: o.x || 0, y: o.y || 0, w: 1, h: 1 };
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const c of o.children) { const b = bbox(c); x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h); }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  return { x: o.x || 0, y: o.y || 0, w: o.w || 0, h: o.h || 0 };
}

export function moveObj(o, dx, dy) {
  if (o.points) o.points = o.points.map(([x, y]) => [x + dx, y + dy]);
  else if (o.type === "group") o.children.forEach((c) => moveObj(c, dx, dy));
  else { o.x = (o.x || 0) + dx; o.y = (o.y || 0) + dy; }
}

export function resizeObj(o, from, to) {
  // map bbox `from` to `to` (both {x,y,w,h})
  const sx = to.w / (from.w || 1), sy = to.h / (from.h || 1);
  const map = (x, y) => [to.x + (x - from.x) * sx, to.y + (y - from.y) * sy];
  if (o.points) o.points = o.points.map(([x, y]) => map(x, y).map((v) => Math.round(v * 10) / 10));
  else if (o.type === "group") o.children.forEach((c) => resizeObj(c, from, to));
  else { const [x, y] = map(o.x, o.y); o.x = r1(x); o.y = r1(y); o.w = r1(Math.max(1, o.w * sx)); o.h = r1(Math.max(1, o.h * sy)); }
}
const r1 = (v) => Math.round(v * 10) / 10;

// ---------------------------------------------------------------- rendering helpers
let gradId = 0;
export function gradientFill(defs, spec) {
  // "90,#aaa,#555" or "radial,#fff,#000"
  if (!spec) return null;
  const p = String(spec).split(",").map((s) => s.trim());
  if (p.length < 3) return null;
  const id = "g" + (++gradId);
  let g;
  if (p[0] === "radial") g = svg("radialGradient", { id });
  else {
    const a = ((+p[0] || 0) * Math.PI) / 180;
    g = svg("linearGradient", { id, x1: 0.5 - Math.cos(a) / 2, y1: 0.5 + Math.sin(a) / 2, x2: 0.5 + Math.cos(a) / 2, y2: 0.5 - Math.sin(a) / 2 });
  }
  const stops = p.slice(1);
  stops.forEach((c, i) => g.append(svg("stop", { offset: stops.length === 1 ? 0 : i / (stops.length - 1), "stop-color": c })));
  defs.append(g);
  return `url(#${id})`;
}

let patId = 0;
function textureFill(defs, o) {
  const fb = o.fillImageBox, id = "tex" + (++patId);
  const b = fb ? { x: fb[0], y: fb[1], w: fb[2], h: fb[3] } : bbox(o);
  const p = svg("pattern", { id, patternUnits: "userSpaceOnUse", x: b.x, y: b.y, width: b.w, height: b.h });
  p.append(svg("image", { href: o.fillImage.startsWith("data:") || o.fillImage.startsWith("http") ? o.fillImage : "/project/" + o.fillImage, x: 0, y: 0, width: b.w, height: b.h, preserveAspectRatio: "none" }));
  defs.append(p);
  return `url(#${id})`;
}

function shapeAttrs(o, defs) {
  const a = { fill: o.fill ?? "none", stroke: o.stroke ?? "none", "stroke-width": o.sw ?? 1 };
  if (o.gradient) a.fill = gradientFill(defs, o.gradient) || a.fill;
  if (o.fillImage) a.fill = textureFill(defs, o);
  if (o.dash) a["stroke-dasharray"] = o.dash;
  if (o.shadow) a.filter = "url(#gf-shadow)";
  return a;
}

function textAnchor(o) { return o.align === "center" ? "middle" : o.align === "right" ? "end" : "start"; }

export function textNode(o, x, y, w, h, text) {
  if (o.stretch) return stretchText(o, x, y, w, h, text);
  const pad = 4;
  const tx = o.align === "center" ? x + w / 2 : o.align === "right" ? x + w - pad : x + pad;
  const fs = o.fs || 14;
  const lines = String(text ?? "").split("\n");
  const lh = fs * 1.2;
  const total = lh * lines.length;
  const ty0 = o.valign === "top" ? y + pad + fs * 0.85 : o.valign === "bottom" ? y + h - pad - total + fs * 0.85 : y + h / 2 - total / 2 + fs * 0.85;
  const t = svg("text", {
    x: tx, y: ty0, "font-size": fs, "font-family": o.font || "Segoe UI, Arial, sans-serif",
    "font-weight": o.fw ? "bold" : null, "font-style": o.italic ? "italic" : null,
    fill: o.color || "#000", "text-anchor": textAnchor(o), "pointer-events": "none",
  });
  setTextLines(t, lines, tx, lh);
  return t;
}

/** "stretch text" (GraphWorX style): the whole block is scaled uniformly so its widest line fills
 * the box width and the lines fill the box height (width measured once the text is in the DOM). */
function stretchText(o, x, y, w, h, text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const lh = h / lines.length;
  const anchor = o.align === "center" ? "middle" : o.align === "right" ? "end" : "start";
  const ax = anchor === "middle" ? x + w / 2 : anchor === "end" ? x + w : x;
  const g = svg("text", { "font-family": o.font || "Arial", "font-weight": o.fw ? "bold" : null, "font-style": o.italic ? "italic" : null, fill: o.color || "#000", "pointer-events": "none", "font-size": lh * 0.86, "text-anchor": anchor });
  const spans = lines.map((ln, i) => { const ts = svg("tspan", { x: ax, y: y + lh * i + lh * 0.80 }); ts.textContent = ln; g.append(ts); return ts; });
  requestAnimationFrame(() => {
    let wmax = 0;
    for (const t of spans) { try { wmax = Math.max(wmax, t.getComputedTextLength()); } catch { /* not rendered yet */ } }
    if (wmax > 0) g.setAttribute("transform", `translate(${ax} 0) scale(${w / wmax} 1) translate(${-ax} 0)`);
  });
  return g;
}

export function setTextLines(t, lines, tx, lh) {
  if (lines.length === 1) { t.textContent = lines[0]; return; }
  t.textContent = "";
  lines.forEach((ln, i) => { const s = svg("tspan", { x: tx, dy: i ? lh : 0 }); s.textContent = ln; t.append(s); });
}

function polarXY(cx, cy, rx, ry, deg) { const a = ((deg - 90) * Math.PI) / 180; return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; }
export function arcPath(cx, cy, rx, ry, a0, a1, type = "arc") {
  const [x0, y0] = polarXY(cx, cy, rx, ry, a0), [x1, y1] = polarXY(cx, cy, rx, ry, a1);
  const large = Math.abs(a1 - a0) % 360 > 180 ? 1 : 0;
  let d = `M${x0},${y0} A${rx},${ry} 0 ${large} 1 ${x1},${y1}`;
  if (type === "pie") d = `M${cx},${cy} L${x0},${y0} A${rx},${ry} 0 ${large} 1 ${x1},${y1} Z`;
  if (type === "chord") d += " Z";
  return d;
}

/**
 * Build the SVG node of an object. Returns { g, parts } where parts holds
 * sub-elements that dynamics update directly (no re-render on value change).
 * ctx: { defs, resolve(text), mode, loadSymbol(src) }
 */
export function renderObject(o, ctx) {
  const g = svg("g", { "data-id": o.id });
  const parts = { g };
  const b = bbox(o);
  const defs = ctx.defs;
  if (o.opacity != null && o.opacity !== "" && +o.opacity < 1) g.setAttribute("opacity", o.opacity);
  if (o.rot) g.setAttribute("transform", `rotate(${o.rot} ${b.x + b.w / 2} ${b.y + b.h / 2})`);
  if (o.invisible) g.setAttribute("data-invisible", "1");   // hidden at runtime, faded in the editor (CSS)
  const R = ctx.resolve || ((s) => s);
  switch (o.type) {
    case "rect": {
      const s = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: o.rx || 0, ...shapeAttrs(o, defs) });
      g.append(s); parts.shape = s; break;
    }
    case "ellipse": {
      const s = svg("ellipse", { cx: o.x + o.w / 2, cy: o.y + o.h / 2, rx: o.w / 2, ry: o.h / 2, ...shapeAttrs(o, defs) });
      g.append(s); parts.shape = s; break;
    }
    case "arc": {
      const s = svg("path", { d: arcPath(o.x + o.w / 2, o.y + o.h / 2, o.w / 2, o.h / 2, o.start || 0, o.end ?? 270, o.arcType), ...shapeAttrs(o, defs) });
      if (o.arcType === "arc") s.setAttribute("fill", "none");
      g.append(s); parts.shape = s; break;
    }
    case "line": case "polyline": case "polygon": {
      const pts = (o.points || []).map((p) => p.join(",")).join(" ");
      const s = svg(o.type === "polygon" ? "polygon" : "polyline", { points: pts, ...shapeAttrs(o, defs), "stroke-linecap": "round", "stroke-linejoin": "round" });
      if (o.type !== "polygon" && (!o.fill || o.fill === "none")) s.setAttribute("fill", "none");
      if (o.arrow && o.arrow !== "none") {
        if (o.arrow === "end" || o.arrow === "both") s.setAttribute("marker-end", "url(#gf-arrow)");
        if (o.arrow === "start" || o.arrow === "both") s.setAttribute("marker-start", "url(#gf-arrow-s)");
      }
      g.append(s); parts.shape = s; break;
    }
    case "pipe": {
      const pts = (o.points || []).map((p) => p.join(",")).join(" ");
      const sw = o.sw || 12;
      const outer = svg("polyline", { points: pts, fill: "none", stroke: "#2b3036", "stroke-width": sw + 2, "stroke-linejoin": "round", "stroke-linecap": "butt" });
      const body = svg("polyline", { points: pts, fill: "none", stroke: o.stroke || "#8a94a3", "stroke-width": sw, "stroke-linejoin": "round" });
      const shine = svg("polyline", { points: pts, fill: "none", stroke: "rgba(255,255,255,.28)", "stroke-width": Math.max(1, sw / 4), "stroke-linejoin": "round", transform: `translate(0 ${-sw / 5})` });
      const flow = svg("polyline", { points: pts, fill: "none", stroke: o.flowColor || "#7fd3ff", "stroke-width": Math.max(2, sw / 3), "stroke-dasharray": `${sw} ${sw}`, class: "gf-flow", style: "display:none" });
      g.append(outer, body, shine, flow); parts.shape = body; parts.flow = flow; break;
    }
    case "text": {
      if ((o.fill && o.fill !== "none") || (o.stroke && o.stroke !== "none")) { const bg = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: o.fill || "none", stroke: o.stroke || "none", rx: o.rx || 0 }); g.append(bg); parts.shape = bg; }
      const t = textNode(o, o.x, o.y, o.w, o.h, R(o.text));
      g.append(t); parts.text = t; break;
    }
    case "value": case "input": {
      const bg = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: o.rx || 0, fill: o.fill || "none", stroke: o.stroke || "none", "stroke-width": o.sw || 1 });
      const t = textNode(o, o.x, o.y, o.w, o.h, ctx.mode === "edit" ? (o.placeholder ?? ((o.type === "input" ? "▸" + formatValue(0, o.fmt) : "###.#") + (o.units ? " " + o.units : ""))) : "");
      g.append(bg, t); parts.shape = bg; parts.text = t;
      if (o.type === "input") g.classList.add("gf-clickable");
      break;
    }
    case "button": {
      const face = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: o.rx ?? 4, fill: o.style === "outline" ? "none" : o.fill, stroke: o.stroke || "none", "stroke-width": o.sw || 1 });
      g.append(face);
      if (o.style !== "flat" && o.style !== "outline") {
        g.append(svg("rect", { x: o.x + 1, y: o.y + 1, width: Math.max(0, o.w - 2), height: Math.max(0, o.h / 2 - 1), rx: Math.max(0, (o.rx ?? 4) - 1), fill: "url(#gf-btn-shine)", "pointer-events": "none" }));
      }
      const t = textNode({ ...o, color: o.color || "#fff" }, o.x, o.y, o.w, o.h, R(o.text));
      g.append(t); parts.shape = face; parts.text = t; g.classList.add("gf-button"); break;
    }
    case "image": {
      const img = svg("image", { x: o.x, y: o.y, width: o.w, height: o.h, href: o.src ? (o.src.startsWith("data:") || o.src.startsWith("http") ? o.src : "/project/" + o.src) : "", preserveAspectRatio: o.fit === "stretch" ? "none" : o.fit === "cover" ? "xMidYMid slice" : "xMidYMid meet" });
      g.append(img);
      if (!o.src && ctx.mode === "edit") g.append(svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: "none", stroke: "#888", "stroke-dasharray": "4 3" }));
      parts.shape = img; break;
    }
    case "bar": {
      const bg = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: o.fill, stroke: o.stroke, "stroke-width": 1 });
      const lv = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: o.barColor });
      g.append(bg, lv);
      if (o.ticks) {
        const vert = o.dir === "up" || o.dir === "down";
        for (let i = 0; i <= 10; i++) {
          const f = i / 10, L = i % 5 === 0 ? 0.35 : 0.18;
          const ln = vert ? svg("line", { x1: o.x + o.w * (1 - L), x2: o.x + o.w, y1: o.y + o.h * f, y2: o.y + o.h * f }) : svg("line", { y1: o.y + o.h * (1 - L), y2: o.y + o.h, x1: o.x + o.w * f, x2: o.x + o.w * f });
          ln.setAttribute("stroke", "rgba(255,255,255,.35)"); g.append(ln);
        }
      }
      parts.shape = bg; parts.level = lv;
      setLevel(parts, o, ctx.mode === "edit" ? 0.6 : 0);
      break;
    }
    case "led": {
      const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
      const s = o.shape === "square" ? svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: 3, fill: o.off, stroke: o.stroke, "stroke-width": o.sw ?? 1 })
        : svg("ellipse", { cx, cy, rx: o.w / 2, ry: o.h / 2, fill: o.off, stroke: o.stroke, "stroke-width": o.sw ?? 1 });
      const hl = svg("ellipse", { cx: cx - o.w * 0.15, cy: cy - o.h * 0.18, rx: o.w * 0.22, ry: o.h * 0.14, fill: "rgba(255,255,255,.35)", "pointer-events": "none" });
      g.append(s, hl); parts.shape = s; break;
    }
    case "gauge": {
      const cx = o.x + o.w / 2, cy = o.y + o.h / 2, r = Math.min(o.w, o.h) / 2;
      g.append(svg("circle", { cx, cy, r: r - 1, fill: o.fill, stroke: o.stroke, "stroke-width": 2 }));
      const a0 = -135, a1 = 135, min = +o.min || 0, max = +o.max || 100;
      const ang = (v) => a0 + ((Math.max(min, Math.min(max, v)) - min) / (max - min || 1)) * (a1 - a0);
      for (const seg of String(o.ranges || "").split(";")) {
        const [f, t, c] = seg.split(",");
        if (c) g.append(svg("path", { d: arcPath(cx, cy, r * 0.8, r * 0.8, ang(+f), ang(+t)), fill: "none", stroke: c, "stroke-width": r * 0.1 }));
      }
      for (let i = 0; i <= 10; i++) {
        const a = a0 + (i / 10) * (a1 - a0), [x0, y0] = polarXY(cx, cy, r * 0.86, r * 0.86, a), [x1, y1] = polarXY(cx, cy, r * (i % 5 ? 0.78 : 0.72), r * (i % 5 ? 0.78 : 0.72), a);
        g.append(svg("line", { x1: x0, y1: y0, x2: x1, y2: y1, stroke: o.color, "stroke-width": i % 5 ? 1 : 2 }));
        if (i % 5 === 0) { const [lx, ly] = polarXY(cx, cy, r * 0.56, r * 0.56, a); const t = svg("text", { x: lx, y: ly + r * 0.04, "font-size": r * 0.12, fill: o.color, "text-anchor": "middle", "font-family": "Segoe UI, Arial" }); t.textContent = formatValue(min + (i / 10) * (max - min), "0"); g.append(t); }
      }
      const needle = svg("g", { transform: `rotate(${ang(ctx.mode === "edit" ? (min + max) / 2 : min)} ${cx} ${cy})` });
      needle.append(svg("path", { d: `M${cx - r * 0.035},${cy} L${cx},${cy - r * 0.78} L${cx + r * 0.035},${cy} Z`, fill: o.needle }), svg("circle", { cx, cy, r: r * 0.07, fill: "#ccc", stroke: "#333" }));
      const vt = svg("text", { x: cx, y: cy + r * 0.78, "font-size": r * 0.16, fill: o.color, "text-anchor": "middle", "font-family": "Consolas, monospace" });
      vt.textContent = ctx.mode === "edit" ? "##.#" : "";
      g.append(needle, vt);
      parts.needle = needle; parts.text = vt; parts.gauge = { cx, cy, ang };
      break;
    }
    case "slider": {
      const vert = o.dir === "vertical";
      const track = vert ? svg("rect", { x: o.x + o.w / 2 - 4, y: o.y + 8, width: 8, height: o.h - 16, rx: 4, fill: o.fill }) : svg("rect", { x: o.x + 8, y: o.y + o.h / 2 - 4, width: o.w - 16, height: 8, rx: 4, fill: o.fill });
      const fillBar = svg("rect", { rx: 4, fill: o.barColor });
      const thumb = svg("circle", { r: Math.min(o.w, o.h) * (vert ? 0.32 : 0.34), fill: "#e9edf2", stroke: "#333", "stroke-width": 1, class: "gf-thumb" });
      g.append(track, fillBar, thumb); parts.track = track; parts.fillBar = fillBar; parts.thumb = thumb;
      setSlider(parts, o, 0.4); break;
    }
    case "toggle": {
      const r = o.h / 2;
      const bg = svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, rx: r, fill: o.off });
      const knob = svg("circle", { cx: o.x + r, cy: o.y + r, r: r - 3, fill: "#fff" });
      g.append(bg, knob); parts.shape = bg; parts.knob = knob; g.classList.add("gf-clickable"); break;
    }
    case "trend": case "histtrend": case "alarms": {
      const fo = svg("foreignObject", { x: o.x, y: o.y, width: o.w, height: o.h });
      const div = el("div", { class: "gf-widget", style: { width: o.w + "px", height: o.h + "px", background: o.fill } });
      fo.append(div); g.append(fo); parts.host = div;
      if (ctx.mode === "edit") div.append(el("div", { class: "gf-widget-label", style: { color: o.color } }, TYPES[o.type].label + (o.pens ? ` (${o.pens.length} pens)` : "")));
      break;
    }
    case "group": {
      for (const c of o.children || []) { const n = renderObject(c, ctx); g.append(n.g); (parts.children ||= []).push(n); }
      break;
    }
    case "symbol": {
      parts.symbolHost = g;
      if (ctx.loadSymbol) ctx.loadSymbol(o, g, parts);
      break;
    }
    default:
      g.append(svg("rect", { x: o.x, y: o.y, width: o.w || 50, height: o.h || 50, fill: "#f0f", opacity: 0.3 }));
  }
  if (o.tooltip) { const t = svg("title"); t.textContent = R(o.tooltip); g.append(t); }
  return parts;
}

export function setLevel(parts, o, frac) {
  frac = Math.max(0, Math.min(1, frac || 0));
  const lv = parts.level;
  const { x, y, w, h } = o;
  if (o.dir === "down") { lv.setAttribute("y", y); lv.setAttribute("height", h * frac); }
  else if (o.dir === "left") { lv.setAttribute("x", x + w * (1 - frac)); lv.setAttribute("width", w * frac); }
  else if (o.dir === "right") { lv.setAttribute("x", x); lv.setAttribute("width", w * frac); }
  else { lv.setAttribute("y", y + h * (1 - frac)); lv.setAttribute("height", h * frac); }
}

export function setSlider(parts, o, frac) {
  frac = Math.max(0, Math.min(1, frac || 0));
  if (o.dir === "vertical") {
    const y0 = o.y + 8, H = o.h - 16, yy = y0 + H * (1 - frac);
    parts.thumb.setAttribute("cx", o.x + o.w / 2); parts.thumb.setAttribute("cy", yy);
    parts.fillBar.setAttribute("x", o.x + o.w / 2 - 4); parts.fillBar.setAttribute("width", 8); parts.fillBar.setAttribute("y", yy); parts.fillBar.setAttribute("height", y0 + H - yy);
  } else {
    const x0 = o.x + 8, W = o.w - 16, xx = x0 + W * frac;
    parts.thumb.setAttribute("cx", xx); parts.thumb.setAttribute("cy", o.y + o.h / 2);
    parts.fillBar.setAttribute("x", x0); parts.fillBar.setAttribute("width", xx - x0); parts.fillBar.setAttribute("y", o.y + o.h / 2 - 4); parts.fillBar.setAttribute("height", 8);
  }
}

export function sharedDefs() {
  const d = svg("defs");
  d.innerHTML = `
    <filter id="gf-shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="2" dy="3" stdDeviation="2" flood-opacity=".45"/></filter>
    <linearGradient id="gf-btn-shine" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity=".04"/></linearGradient>
    <marker id="gf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>
    <marker id="gf-arrow-s" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>
    <pattern id="gf-badq" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="rgba(255,80,80,.35)"/></pattern>`;
  return d;
}
