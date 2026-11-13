// DisplayView: renders a .nll display (SVG) and, in run mode, binds dynamics + actions.
import { svg, el, formatValue, truthy, mixColor, toast, clamp } from "./util.js";
import { compile, exprOf, resolveAliases } from "./expr.js";
import { renderObject, sharedDefs, setLevel, setSlider, bbox, TYPES } from "./objects.js";
import { Trend, PALETTE } from "./trend.js";
import { api, Q } from "./net.js";

const symbolCache = new Map();   // path -> Promise<doc>
export function loadDoc(path, fresh = false) {
  if (fresh) symbolCache.delete(path);
  if (!symbolCache.has(path)) symbolCache.set(path, api.text(`/api/file?path=${encodeURIComponent(path)}`).then((t) => JSON.parse(t)).catch((e) => { symbolCache.delete(path); throw e; }));
  return symbolCache.get(path);
}
export function invalidateDoc(path) { symbolCache.delete(path); }

/** Evaluate a color dynamic definition. */
export function colorFrom(def, v) {
  const t = def.type || (def.map ? "map" : def.stops ? "range" : def.from ? "blend" : "bool");
  if (t === "bool") return truthy(v) ? def.on : def.off;
  if (t === "map") { const k = typeof v === "boolean" ? (v ? "1" : "0") : String(v); return (def.map && def.map[k]) ?? def.default; }
  if (t === "blend") return mixColor(def.from, def.to, (Number(v) - (+def.min || 0)) / ((+def.max || 1) - (+def.min || 0) || 1));
  if (t === "range") { let c = def.default; for (const [lim, col] of def.stops || []) if (Number(v) >= +lim) c = col; return c; }
  return undefined;
}

function lin(v, inr, outr) {
  const [a, b] = inr || [0, 100], [c, d] = outr || [0, 100];
  const r = c + ((clamp(Number(v), Math.min(a, b), Math.max(a, b)) - a) * (d - c)) / (b - a || 1);
  return Number.isFinite(r) ? r : c;
}

export class DisplayView {
  constructor({ conn, host, mode = "run", app, aliases = {}, popup = null }) {
    this.conn = conn; this.host = host; this.mode = mode; this.app = app;
    this.instanceAliases = aliases; this.popup = popup;
    this.subs = [];            // [ref, fn]
    this.bindings = [];
    this.dirty = new Set();
    this.widgets = [];
    this.timers = [];
    this.nodes = new Map();    // id -> parts (top level + nested)
    this.module = null;
    this.doc = null;
  }

  get scopes() {
    const g = (this.conn.aliases && this.conn.aliases[this.app?.aliasSet || "Default"]) || {};
    return [this.instanceAliases, this.doc?.aliases, g, this.conn.aliases?.Default];
  }

  async load(path) {
    this.path = path;
    const doc = await loadDoc(path, true);
    await this.setDoc(doc);
    return doc;
  }

  async setDoc(doc) {
    this.destroy(false);
    this.doc = doc;
    const [W, H] = doc.size || [1280, 720];
    this.svg = svg("svg", { class: "gf-display", viewBox: `0 0 ${W} ${H}`, width: W, height: H, "data-mode": this.mode });
    this.svg.style.background = doc.background || "#2b2f36";
    this.defs = sharedDefs();
    this.svg.append(this.defs);
    this.layer = svg("g", { class: "gf-layer" });
    this.svg.append(this.layer);
    this.host.innerHTML = "";
    this.host.append(this.svg);
    this.renderInto(doc.objects || [], this.layer, this.scopes);
    if (this.mode === "run") {
      this._flush();
      if (doc.script) await this._loadScript(doc.script);
      this._runActions(doc.onOpen);
      this.timers.push(setInterval(() => this.widgets.forEach((w) => w.tick && w.tick()), 1000));
    }
  }

  destroy(full = true) {
    if (this.module?.onClose) try { this.module.onClose(this.ctx()); } catch (e) { console.error(e); }
    if (this.mode === "run" && this.doc?.onClose && full) this._runActions(this.doc.onClose);
    for (const [ref, fn] of this.subs) this.conn.unsubscribe(ref, fn);
    this.subs = []; this.bindings = []; this.dirty.clear();
    this.widgets.forEach((w) => w.destroy && w.destroy()); this.widgets = [];
    this.timers.forEach(clearInterval); this.timers = [];
    this.nodes.clear();
    this.module = null;
    if (full && this.host) this.host.innerHTML = "";
  }

  // ------------------------------------------------------------------ rendering
  renderInto(objects, parent, scopes) {
    for (const o of objects) {
      const parts = renderObject(o, {
        defs: this.defs, mode: this.mode,
        resolve: (t) => resolveAliases(t, scopes),
        loadSymbol: (so, g, p) => this._symbol(so, g, p, scopes),
      });
      parent.append(parts.g);
      this.nodes.set(o.id, { o, parts });
      if (this.mode === "run") {
        this._bindObject(o, parts, scopes);
        if (o.type === "group" && parts.children) {
          // nested objects of groups already rendered; bind them too
          const walk = (list, ps) => list.forEach((c, i) => { const cp = ps[i]; if (!cp) return; this.nodes.set(c.id, { o: c, parts: cp }); this._bindObject(c, cp, scopes); if (c.type === "group") walk(c.children || [], cp.children || []); });
          walk(o.children || [], parts.children);
        }
      }
    }
  }

  async _symbol(o, g, parts, outerScopes) {
    if (!o.src) { g.append(svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: "none", stroke: "#f0f", "stroke-dasharray": "4 3" })); return; }
    let sdoc;
    try { sdoc = await loadDoc(o.src); } catch (e) {
      g.append(svg("rect", { x: o.x, y: o.y, width: o.w, height: o.h, fill: "rgba(255,0,0,.2)", stroke: "red" }));
      return;
    }
    const [sw, sh] = sdoc.size || [100, 100];
    const inner = svg("g", { transform: `translate(${o.x} ${o.y}) scale(${o.w / sw} ${o.h / sh})` });
    g.append(inner);
    const local = {};
    for (const [k, v] of Object.entries(o.aliases || {})) local[k] = resolveAliases(String(v), outerScopes);
    const scopes = [local, sdoc.aliases || {}, ...outerScopes];
    this.renderInto(sdoc.objects || [], inner, scopes);
    if (this.mode === "run") this._schedule();
  }

  /** Edit mode fast path: re-render only the given objects in place. */
  refreshObjects(objs) {
    for (const o of objs) {
      const n = this.nodes.get(o.id);
      if (!n) return false;
      const parts = renderObject(o, { defs: this.defs, mode: this.mode, resolve: (t) => resolveAliases(t, this.scopes), loadSymbol: (so, g, p) => this._symbol(so, g, p, this.scopes) });
      n.parts.g.replaceWith(parts.g);
      this.nodes.set(o.id, { o, parts });
    }
    return true;
  }

  // ------------------------------------------------------------------ bindings
  _bind(exprSrc, scopes, apply, opts = {}) {
    const src = resolveAliases(exprSrc, scopes);
    const c = compile(src);
    const b = { c, apply, points: [], last: undefined, lastQ: -1, opts };
    if (c.constant) { try { apply(c.fn([]), Q.GOOD); } catch (e) { console.warn(e.message); } return b; }
    for (const ref of c.refs) {
      const fn = () => { this.dirty.add(b); this._schedule(); };
      const p = this.conn.subscribe(ref, fn, { history: !!opts.history });
      b.points.push(p);
      this.subs.push([ref, fn]);
    }
    this.bindings.push(b);
    this.dirty.add(b);
    return b;
  }

  _schedule() { if (!this._mq) { this._mq = true; queueMicrotask(() => { this._mq = false; this._flush(); }); } }

  _flush() {
    for (const b of this.dirty) {
      let q = Q.GOOD;
      const vals = b.points.map((p) => { if (p.q < q) q = p.q; return p.value; });
      let v;
      if (q < Q.GOOD && b.points.some((p) => p.value === undefined || p.value === null)) { v = undefined; }
      else { try { v = b.c.fn(vals); } catch (e) { v = undefined; q = Q.CONFIG; } }
      if (v === b.last && q === b.lastQ && !b.opts.always) continue;
      b.last = v; b.lastQ = q;
      try { b.apply(v, q, b); } catch (e) { console.error(e); }
    }
    this.dirty.clear();
  }

  _quality(parts, q, ref) {
    const bad = q < Q.GOOD;
    parts.g.classList.toggle("gf-badq", bad && q !== Q.NOT_CONNECTED);
    parts.g.classList.toggle("gf-noconn", q === Q.NOT_CONNECTED);
    if (bad && ref) parts.g.dataset.badref = ref; else delete parts.g.dataset.badref;
  }

  _bindObject(o, parts, scopes) {
    const dyn = o.dyn || {};
    const st = { dx: 0, dy: 0, rot: 0, sx: 1, sy: 1 };
    const b0 = bbox(o);
    const applyT = () => {
      const cx = b0.x + b0.w / 2, cy = b0.y + b0.h / 2;
      let t = "";
      if (st.dx || st.dy) t += `translate(${st.dx} ${st.dy}) `;
      const r = (o.rot || 0) + st.rot;
      if (r) t += `rotate(${r} ${st.rcx ?? cx} ${st.rcy ?? cy}) `;
      if (st.sx !== 1 || st.sy !== 1) { const ax = st.ax ?? b0.x, ay = st.ay ?? b0.y; t += `translate(${ax} ${ay}) scale(${st.sx} ${st.sy}) translate(${-ax} ${-ay})`; }
      parts.g.setAttribute("transform", t.trim());
    };
    const firstRef = (src) => compile(resolveAliases(src, scopes)).refs[0];

    // ---- type specific value bindings
    const vexpr = exprOf(o.expr != null ? { expr: o.expr } : o.tag ? { tag: o.tag } : null);
    switch (o.type) {
      case "value": case "input":
        if (vexpr) this._bind(vexpr, scopes, (v, q) => {
          this._quality(parts, q, firstRef(vexpr));
          parts.text.textContent = q === Q.NOT_CONNECTED && v === undefined ? "···" : v === undefined ? "????" : formatValue(v, o.fmt) + (o.units ? " " + o.units : "");
        });
        if (o.type === "input") this._onPointer(parts.g, "click", () => this.app.dataEntry({ tag: resolveAliases(o.tag, scopes), min: o.min, max: o.max, fmt: o.fmt, confirm: o.confirm, title: o.title }));
        break;
      case "bar":
        if (vexpr) this._bind(vexpr, scopes, (v, q) => { this._quality(parts, q, firstRef(vexpr)); setLevel(parts, o, (Number(v) - (+o.min || 0)) / ((+o.max || 100) - (+o.min || 0) || 1)); });
        break;
      case "led":
        if (vexpr) this._bind(vexpr, scopes, (v, q) => { this._quality(parts, q, firstRef(vexpr)); const on = truthy(v); parts.shape.setAttribute("fill", on ? o.on : o.off); parts.g.classList.toggle("gf-blink", !!(on && o.blinkOn)); });
        break;
      case "gauge":
        if (vexpr) this._bind(vexpr, scopes, (v, q) => {
          this._quality(parts, q, firstRef(vexpr));
          const { cx, cy, ang } = parts.gauge;
          parts.needle.setAttribute("transform", `rotate(${ang(Number(v) || 0)} ${cx} ${cy})`);
          parts.text.textContent = v === undefined ? "???" : formatValue(v, o.fmt) + (o.units ? " " + o.units : "");
        });
        break;
      case "pipe":
        if (o.flow) this._bind(o.flow, scopes, (v) => { parts.flow.style.display = truthy(v) ? "" : "none"; });
        break;
      case "slider": {
        const tag = resolveAliases(o.tag || "", scopes);
        if (!tag) break;
        let dragging = false;
        const min = +o.min || 0, max = o.max === "" || o.max == null ? 100 : +o.max;
        this._bind(`{{${tag}}}`, scopes, (v, q) => { this._quality(parts, q, tag); if (!dragging) setSlider(parts, o, (Number(v) - min) / (max - min || 1)); });
        const toVal = (e) => {
          const pt = this.toLocal(e);
          const f = o.dir === "vertical" ? 1 - (pt.y - (o.y + 8)) / (o.h - 16) : (pt.x - (o.x + 8)) / (o.w - 16);
          let v = min + clamp(f, 0, 1) * (max - min);
          const step = +o.step || 0; if (step) v = Math.round(v / step) * step;
          return { v: +v.toFixed(6), f: clamp(f, 0, 1) };
        };
        let lastSent = 0;
        parts.g.style.cursor = "pointer";
        parts.g.addEventListener("pointerdown", (e) => {
          if (!this.app.canWrite()) return;
          dragging = true; parts.g.setPointerCapture(e.pointerId);
          const move = (ev) => { const { v, f } = toVal(ev); setSlider(parts, o, f); if (Date.now() - lastSent > 150) { lastSent = Date.now(); this.app.write(tag, v); } };
          const up = (ev) => { dragging = false; parts.g.removeEventListener("pointermove", move); parts.g.removeEventListener("pointerup", up); this.app.write(tag, toVal(ev).v); };
          parts.g.addEventListener("pointermove", move); parts.g.addEventListener("pointerup", up);
          move(e);
        });
        break;
      }
      case "toggle": {
        const tag = resolveAliases(o.tag || "", scopes);
        if (!tag) break;
        let cur = false;
        this._bind(`{{${tag}}}`, scopes, (v, q) => {
          this._quality(parts, q, tag);
          cur = String(v) === String(o.onValue ?? 1) || (o.onValue == null && truthy(v)) || (v === true && String(o.onValue) === "1");
          parts.shape.setAttribute("fill", cur ? o.on : o.off);
          const r = o.h / 2;
          parts.knob.setAttribute("cx", cur ? o.x + o.w - r : o.x + r);
        });
        this._onPointer(parts.g, "click", async () => {
          const val = cur ? (o.offValue ?? 0) : (o.onValue ?? 1);
          if (o.confirm && !(await this.app.confirm(`Set ${tag} to ${val}?`))) return;
          this.app.write(tag, isNaN(+val) ? val : +val);
        });
        break;
      }
      case "trend": this._trendWidget(o, parts, scopes); break;
      case "histtrend": this._histWidget(o, parts, scopes); break;
      case "alarms": this._alarmWidget(o, parts); break;
    }

    // ---- generic dynamics
    const colorTarget = (key) => key === "color" ? parts.text : key === "stroke" ? parts.shape : parts.shape;
    for (const key of ["fill", "stroke", "color"]) {
      const d = dyn[key];
      if (!d) continue;
      const tgt = colorTarget(key);
      if (!tgt) continue;
      const attr = key === "stroke" ? "stroke" : "fill";
      this._bind(exprOf(d), scopes, (v, q) => { this._quality(parts, q, firstRef(exprOf(d))); const c = v === undefined ? (d.bad || null) : colorFrom(d, v); if (c) tgt.setAttribute(attr, c); });
    }
    if (dyn.visible) this._bind(exprOf(dyn.visible), scopes, (v) => { const vis = truthy(v) !== !!dyn.visible.invert; parts.g.style.display = vis ? "" : "none"; });
    if (dyn.blink) this._bind(exprOf(dyn.blink), scopes, (v) => parts.g.classList.toggle(dyn.blink.fast ? "gf-blink-fast" : "gf-blink", truthy(v)));
    if (dyn.opacity) this._bind(exprOf(dyn.opacity), scopes, (v) => parts.g.setAttribute("opacity", lin(v, dyn.opacity.in || [0, 1], dyn.opacity.out || [0, 1])));
    if (dyn.text && parts.text) this._bind(exprOf(dyn.text), scopes, (v, q) => { this._quality(parts, q, firstRef(exprOf(dyn.text))); parts.text.textContent = v === undefined ? "????" : formatValue(v, dyn.text.fmt) + (dyn.text.units ? " " + dyn.text.units : ""); });
    if (dyn.x) this._bind(exprOf(dyn.x), scopes, (v) => { st.dx = lin(v, dyn.x.in, dyn.x.out); applyT(); });
    if (dyn.y) this._bind(exprOf(dyn.y), scopes, (v) => { st.dy = lin(v, dyn.y.in, dyn.y.out); applyT(); });
    if (dyn.rot) {
      if (dyn.rot.cx != null) { st.rcx = b0.x + b0.w * dyn.rot.cx; st.rcy = b0.y + b0.h * (dyn.rot.cy ?? 0.5); }
      this._bind(exprOf(dyn.rot), scopes, (v) => { st.rot = lin(v, dyn.rot.in, dyn.rot.out || [0, 360]); applyT(); });
    }
    if (dyn.scale) {
      const an = dyn.scale.anchor || "bottom";
      st.ax = an.includes("right") ? b0.x + b0.w : an === "center" ? b0.x + b0.w / 2 : b0.x;
      st.ay = an.includes("bottom") ? b0.y + b0.h : an === "center" ? b0.y + b0.h / 2 : b0.y;
      this._bind(exprOf(dyn.scale), scopes, (v) => { const f = lin(v, dyn.scale.in || [0, 100], dyn.scale.out || [0, 1]); st.sx = dyn.scale.dir === "v" ? 1 : f; st.sy = dyn.scale.dir === "h" ? 1 : f; applyT(); });
    }
    if (dyn.level && parts.shape) this._levelDyn(o, parts, dyn.level, scopes);
    if (dyn.enabled) this._bind(exprOf(dyn.enabled), scopes, (v) => parts.g.classList.toggle("gf-disabled", !truthy(v)));
    if (dyn.animate && parts.children && parts.children.length > 1) {   // GraphWorX "Animator": cycle child frames
      const kids = parts.children.map((c) => c.g);
      let timer = 0, idx = 0;
      const show = (i) => kids.forEach((g, k) => { g.style.display = k === i ? "" : "none"; });
      show(+dyn.animate.offFrame || 0);
      this._bind(exprOf(dyn.animate), scopes, (v) => {
        const on = truthy(v);
        if (on && !timer) { timer = setInterval(() => { idx = (idx + 1) % kids.length; show(idx); }, +dyn.animate.ms || 500); this.timers.push(timer); }
        else if (!on && timer) { clearInterval(timer); timer = 0; show(+dyn.animate.offFrame || 0); }
      });
    }

    // ---- actions
    if (o.actions && o.actions.length) this._bindActions(o, parts, scopes);
  }

  _levelDyn(o, parts, d, scopes) {
    const b = bbox(o);
    const id = "clip" + Math.random().toString(36).slice(2, 8);
    const cp = svg("clipPath", { id });
    const r = svg("rect", { x: b.x, y: b.y + b.h, width: b.w, height: 0 });
    cp.append(r); this.defs.append(cp);
    const over = parts.shape.cloneNode();
    over.setAttribute("fill", d.color || "#3fa7ff"); over.setAttribute("stroke", "none"); over.setAttribute("clip-path", `url(#${id})`);
    over.removeAttribute("filter");
    parts.shape.after(over);
    const dir = d.dir || "up";
    this._bind(exprOf(d), scopes, (v) => {
      const f = clamp((Number(v) - (d.in?.[0] ?? 0)) / ((d.in?.[1] ?? 100) - (d.in?.[0] ?? 0) || 1), 0, 1) || 0;
      if (dir === "up") { r.setAttribute("y", b.y + b.h * (1 - f)); r.setAttribute("height", b.h * f); r.setAttribute("x", b.x); r.setAttribute("width", b.w); }
      else if (dir === "down") { r.setAttribute("y", b.y); r.setAttribute("height", b.h * f); r.setAttribute("x", b.x); r.setAttribute("width", b.w); }
      else if (dir === "right") { r.setAttribute("x", b.x); r.setAttribute("width", b.w * f); r.setAttribute("y", b.y); r.setAttribute("height", b.h); }
      else { r.setAttribute("x", b.x + b.w * (1 - f)); r.setAttribute("width", b.w * f); r.setAttribute("y", b.y); r.setAttribute("height", b.h); }
    });
  }

  _onPointer(g, kind, fn) {
    g.classList.add("gf-clickable");
    g.addEventListener(kind, (e) => { e.stopPropagation(); if (!g.classList.contains("gf-disabled")) fn(e); });
  }

  _bindActions(o, parts, scopes) {
    const g = parts.g;
    g.classList.add("gf-clickable");
    const by = (on) => o.actions.filter((a) => (a.on || "click") === on);
    const run = (list, e) => { if (g.classList.contains("gf-disabled")) return; e && e.stopPropagation(); this._runActions(list, scopes, o); };
    const down = by("down"), up = by("up"), click = by("click"), dbl = by("dblclick");
    if (down.length || up.length) {
      g.addEventListener("pointerdown", (e) => { g.setPointerCapture(e.pointerId); run(down, e); });
      g.addEventListener("pointerup", (e) => run(up, e));
    }
    if (click.length) g.addEventListener("click", (e) => run(click, e));
    if (dbl.length) g.addEventListener("dblclick", (e) => run(dbl, e));
    const hover = o.actions.find((a) => a.on === "hover");
    if (hover) { g.addEventListener("pointerenter", () => g.classList.add("gf-hover")); g.addEventListener("pointerleave", () => g.classList.remove("gf-hover")); }
  }

  async _runActions(list, scopes = this.scopes, obj = null) {
    if (!list) return;
    for (const a of list) {
      try {
        if (a.role && !this.app.hasRole(a.role)) { toast(`'${a.role}' role required`, "warn"); return; }
        if (a.confirm && !(await this.app.confirm(resolveAliases(a.confirm, scopes)))) return;
        await this._action(a, scopes, obj);
      } catch (e) { toast(String(e.message || e), "error"); }
    }
  }

  async _action(a, scopes, obj) {
    const R = (s) => resolveAliases(s == null ? "" : String(s), scopes);
    const evalv = (src) => { const c = compile(R(src)); return c.fn(c.refs.map((r) => this.conn.value(r))); };
    switch (a.type) {
      case "write": return this.app.write(R(a.tag), a.value === undefined ? 1 : evalv(a.value));
      case "toggle": { const t = R(a.tag); const cur = this.conn.value(t); const p = this.conn.points.get(t); if (!p) { this.conn.subscribe(t, () => {}); } return this.app.write(t, truthy(cur) ? (a.off ?? 0) : (a.on ?? 1)); }
      case "input": return this.app.dataEntry({ tag: R(a.tag), min: a.min, max: a.max, fmt: a.fmt, title: R(a.prompt || a.tag), confirm: a.confirmWrite });
      case "open": {
        const al = {};
        for (const [k, v] of Object.entries(a.aliases || {})) al[k] = R(v);
        return this.app.openDisplay(R(a.display), { target: a.target || "main", aliases: al, title: R(a.title || ""), x: a.x, y: a.y, modal: a.modal });
      }
      case "close": return this.popup ? this.popup.close() : this.app.back();
      case "back": return this.app.back();
      case "forward": return this.app.forward();
      case "script": {
        if (!this.module || typeof this.module[a.fn] !== "function") throw new Error(`display script has no function '${a.fn}'`);
        return this.module[a.fn](this.ctx(scopes, obj), a.args || {});
      }
      case "py": return this.conn.rpc("py", { name: a.fn, args: a.args || {} }).then((r) => r !== null && r !== undefined && a.showResult && toast(String(r)));
      case "aliasSet": return this.app.setAliasSet(R(a.set));
      case "ack": return this.app.ackAlarms(a.ids || null);
      case "recipe": return this.app.recipe(R(a.name), a.mode || "apply");
      case "url": return window.open(R(a.href), a.target || "_blank");
      case "login": return this.app.login();
      case "logout": return this.app.logout();
      case "historian": return this.app.openHistorian(a.pens ? a.pens.map(R) : []);
      default: throw new Error(`unknown action '${a.type}'`);
    }
  }

  // ------------------------------------------------------------------ scripts
  async _loadScript(path) {
    try {
      this.module = await import(`/project/${path}?v=${Date.now()}`);
      if (this.module.onOpen) await this.module.onOpen(this.ctx());
    } catch (e) { console.error(e); toast(`script ${path}: ${e.message}`, "error"); }
  }

  ctx(scopes = this.scopes, obj = null) {
    const view = this;
    return {
      view, obj, conn: this.conn,
      read: (ref) => this.conn.value(resolveAliases(ref, scopes)),
      write: (ref, v) => this.app.write(resolveAliases(ref, scopes), v),
      subscribe: (ref, fn) => { const r = resolveAliases(ref, scopes); const f = (p) => fn(p.value, p.q); this.conn.subscribe(r, f); this.subs.push([r, f]); },
      open: (path, opts = {}) => this.app.openDisplay(path, opts),
      close: () => (this.popup ? this.popup.close() : null),
      alias: (name) => resolveAliases(`<#${name}#>`, scopes),
      py: (name, args) => this.conn.rpc("py", { name, args }),
      toast,
      get: (id) => this.nodes.get(id)?.parts.g,
      set: (id, props) => {
        const n = this.nodes.get(id);
        if (!n) return;
        const p = n.parts;
        if (props.fill && p.shape) p.shape.setAttribute("fill", props.fill);
        if (props.stroke && p.shape) p.shape.setAttribute("stroke", props.stroke);
        if (props.text != null && p.text) p.text.textContent = props.text;
        if (props.visible != null) p.g.style.display = props.visible ? "" : "none";
        if (props.opacity != null) p.g.setAttribute("opacity", props.opacity);
      },
      every: (ms, fn) => { const t = setInterval(() => fn(), ms); this.timers.push(t); return t; },
    };
  }

  // ------------------------------------------------------------------ widgets
  _trendWidget(o, parts, scopes) {
    const tr = new Trend(parts.host, { bg: o.fill, grid: o.grid, text: o.color, legend: o.legend !== false, ymin: o.ymin, ymax: o.ymax, span: (+o.span || 300) * 1000 });
    const pens = (o.pens || []).map((p, i) => ({ name: resolveAliases(p.name || p.expr || `Pen ${i + 1}`, scopes).replace(/\{\{|\}\}/g, ""), color: p.color || PALETTE[i % PALETTE.length], width: p.width, step: p.step }));
    tr.setPens(pens);
    const last = [];
    (o.pens || []).forEach((p, i) => {
      const src = exprOf(p);
      if (!src) return;
      const one = compile(resolveAliases(src, scopes));
      const single = one.refs.length === 1 && one.src === `{{${one.refs[0]}}}`;
      this._bind(src, scopes, (v, q) => { if (q >= Q.GOOD && typeof v !== "object") { const n = typeof v === "boolean" ? +v : Number(v); last[i] = n; tr.append(i, Date.now(), n); tr.setLegendValues(last.map((x) => (x == null ? null : +x.toPrecision(5)))); } }, { history: single, always: true });
      if (single) {
        // backfill from the server's in-memory history so the trend is not empty on open
        setTimeout(() => this.conn.rpc("history", { ref: one.refs[0], seconds: +o.span || 300 }).then((h) => {
          const pen = tr.pens[i];
          if (h && h.t.length) {
            const lastT = h.t[h.t.length - 1], k = pen.t.findIndex((t) => t > lastT);
            pen.t = h.t.concat(k < 0 ? [] : pen.t.slice(k)); pen.v = h.v.concat(k < 0 ? [] : pen.v.slice(k));
            tr.draw();
          }
        }).catch(() => {}), 400);
      }
    });
    // keep flat lines moving: repeat last value every second
    const w = { destroy: () => tr.destroy(), tick: () => { last.forEach((v, i) => v != null && tr.append(i, Date.now(), v)); tr.tick(); } };
    this.widgets.push(w);
  }

  _histWidget(o, parts, scopes) {
    const tr = new Trend(parts.host, { bg: o.fill, grid: o.grid, text: o.color, legend: o.legend !== false, ymin: o.ymin, ymax: o.ymax, span: (+o.span || 3600) * 1000, live: o.live !== false });
    const pens = (o.pens || []).map((p, i) => ({ desc: resolveAliases(p.desc || "", scopes), name: resolveAliases(p.name || p.desc || "", scopes), color: p.color || PALETTE[i % PALETTE.length] }));
    tr.setPens(pens);
    let seq = 0, timer = 0;
    const load = async () => {
      if (!pens.length) return;
      const my = ++seq;
      const t0 = tr.t0, t1 = tr.t1, px = Math.round(tr.W || 800);
      const q = pens.map((p) => "d=" + encodeURIComponent(p.desc)).join("&");
      try {
        const r = await api.get(`/api/hist/data?${q}&t0=${Math.round(t0)}&t1=${Math.round(t1)}&px=${px}`);
        if (my !== seq) return;
        pens.forEach((p, i) => { const s = r.series[p.desc]; if (s) tr.setData(i, s.t, s.v, s.type === 0); });
      } catch (e) { parts.host.title = "historian: " + e.message; }
    };
    tr.onRange = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
    load();
    let n = 0;
    this.widgets.push({ destroy: () => tr.destroy(), tick: () => { tr.tick(); if (tr.o.live && ++n % 10 === 0) load(); } });
  }

  _alarmWidget(o, parts) {
    const host = parts.host;
    host.classList.add("gf-alarmwidget");
    host.style.fontSize = (o.fs || 13) + "px"; host.style.color = o.color;
    const render = () => {
      const f = (o.filter || "").toLowerCase();
      const list = (this.app.alarms || []).filter((a) => !f || (a.group || "").toLowerCase().includes(f) || a.msg.toLowerCase().includes(f) || a.tag.toLowerCase().includes(f));
      host.innerHTML = "";
      const tbl = el("table", { class: "gf-alarm-table" },
        el("tr", {}, ["Time", "Prio", "Tag", "Message", "Value", "State"].map((h) => el("th", {}, h))));
      for (const a of list) {
        const tr = el("tr", { class: `al ${a.active ? "act" : "rtn"} ${a.acked ? "ack" : "unack"} p${a.prio >= 900 ? "hi" : a.prio >= 700 ? "med" : "lo"}`, title: "double click: acknowledge" },
          el("td", {}, new Date(a.t).toLocaleTimeString()), el("td", {}, a.prio), el("td", {}, a.tag), el("td", {}, a.msg),
          el("td", {}, a.value == null ? "" : formatValue(a.value)), el("td", {}, (a.active ? "ACTIVE" : "RTN") + (a.acked ? " / ACK" : "")));
        tr.ondblclick = () => this.app.ackAlarms([a.id]);
        tbl.append(tr);
      }
      host.append(tbl);
    };
    render();
    const fn = () => render();
    this.app.addEventListener("alarms", fn);
    this.widgets.push({ destroy: () => this.app.removeEventListener("alarms", fn) });
  }

  // ------------------------------------------------------------------ utils
  toLocal(e) {
    const pt = this.svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    return pt.matrixTransform(this.layer.getScreenCTM().inverse());
  }
}

export { TYPES };
