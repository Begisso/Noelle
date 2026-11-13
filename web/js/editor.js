// Graphic editor for .nll displays.
import { svg, el, uid, deep, clamp, toast, prompt, modal, confirmBox } from "./util.js";
import { TYPES, bbox, moveObj, resizeObj } from "./objects.js";
import { DisplayView } from "./view.js";
import { api } from "./net.js";

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const SNAP_PX = 6;

export class Editor extends EventTarget {
  constructor(app, host) {
    super();
    this.app = app;
    this.host = host;
    this.zoom = 1;
    this.autoFit = true;       // zoom follows the viewport size until the user zooms by hand
    this.grid = 10;
    this.snap = true;
    this.showGrid = true;
    this.guides = true;
    this.sel = new Set();
    this.undoStack = []; this.redoStack = [];
    this.tool = null;          // type name being created
    this.clipboard = null;
    this.dirty = false;
    host.classList.add("ed-host");
    this.scroller = el("div", { class: "ed-scroller", tabindex: 0 });
    this.stage = el("div", { class: "ed-stage" });
    this.canvasHost = el("div", { class: "ed-canvas" });
    this.overlay = svg("svg", { class: "ed-overlay" });
    this.stage.append(this.canvasHost, this.overlay);
    this.scroller.append(this.stage);
    host.append(this.scroller);
    this.view = new DisplayView({ conn: app.conn, host: this.canvasHost, mode: "edit", app });
    this._bindEvents();
    // The editor area changes size after the first layout (window opening/maximizing, page switch,
    // panel resize). While in fit mode, keep the whole display filling the visible area.
    new ResizeObserver(() => { if (this.autoFit && this.doc) this.fit(); }).observe(this.scroller);
  }

  // ------------------------------------------------------------------ document
  async open(path, doc) {
    this.path = path;
    this.doc = doc;
    this.sel.clear();
    this.undoStack = []; this.redoStack = [];
    this.dirty = false;
    this._ensureIds(doc.objects);
    this.grid = doc.grid || 10;
    this.autoFit = true;
    await this.render();
    this.fit();
    this._changed();
  }

  _ensureIds(list) {
    const seen = new Set();
    const walk = (arr) => arr.forEach((o) => { if (!o.id || seen.has(o.id)) o.id = uid(o.type?.[0] || "o"); seen.add(o.id); if (o.children) walk(o.children); });
    walk(list || []);
  }

  get objects() { if (!this.doc) return []; return this.doc.objects || (this.doc.objects = []); }
  find(id) { return this.objects.find((o) => o.id === id); }
  get selected() { return this.objects.filter((o) => this.sel.has(o.id)); }

  async render() {
    await this.view.setDoc(this.doc);
    const [W, H] = this.doc.size || [1280, 720];
    this.W = W; this.H = H;
    this.overlay.setAttribute("viewBox", `0 0 ${W} ${H}`);
    if (this.autoFit) this._fitZoom();      // display size may have changed
    this._applyZoom();
    this.drawOverlay();
  }

  _applyZoom() {
    const W = this.W, H = this.H, z = this.zoom;
    this.stage.style.width = W * z + "px"; this.stage.style.height = H * z + "px";
    for (const s of [this.view.svg, this.overlay]) { if (!s) continue; s.setAttribute("width", W * z); s.setAttribute("height", H * z); }
    this.dispatchEvent(new Event("zoom"));
  }

  setZoom(z, cx, cy) {
    const old = this.zoom;
    z = clamp(z, 0.1, 8);
    this.autoFit = false;
    const sc = this.scroller;
    const px = cx ?? sc.clientWidth / 2, py = cy ?? sc.clientHeight / 2;
    const docX = (sc.scrollLeft + px - this.stage.offsetLeft) / old, docY = (sc.scrollTop + py - this.stage.offsetTop) / old;
    this.zoom = z;
    this._applyZoom();
    sc.scrollLeft = docX * z + this.stage.offsetLeft - px;
    sc.scrollTop = docY * z + this.stage.offsetTop - py;
    this.drawOverlay();
  }

  // Zoom so the whole display fills the visible editor area (Fit button, Ctrl+0, default on open).
  fit() {
    this.autoFit = true;
    if (!this.W || !this._fitZoom()) return;
    this._applyZoom();
    this.drawOverlay();
  }

  _fitZoom() {
    const sc = this.scroller, cs = getComputedStyle(sc);
    const w = sc.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const h = sc.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    if (w <= 0 || h <= 0) return false;     // page hidden / not laid out yet: ResizeObserver refits later
    // round down to 0.1 % so sub-pixel overflow never produces scrollbars
    this.zoom = clamp(Math.floor(Math.min(w / this.W, h / this.H) * 1000) / 1000, 0.1, 8);
    return true;
  }

  // ------------------------------------------------------------------ undo / commit
  snapshot() { return JSON.stringify(this.doc); }
  begin() { this._before = this.snapshot(); }
  async commit(label = "edit", rerender = true) {
    const before = this._before ?? null;
    this._before = null;
    const now = this.snapshot();
    if (before && before !== now) {
      this.undoStack.push(before);
      if (this.undoStack.length > 300) this.undoStack.shift();
      this.redoStack = [];
      this.dirty = true;
    }
    if (rerender) await this.render(); else this.drawOverlay();
    this._changed(label);
  }
  async edit(fn, label) { this.begin(); fn(); await this.commit(label); }
  async undo() { if (!this.undoStack.length) return; this.redoStack.push(this.snapshot()); this.doc = JSON.parse(this.undoStack.pop()); this.dirty = true; this._pruneSel(); await this.render(); this._changed("undo"); }
  async redo() { if (!this.redoStack.length) return; this.undoStack.push(this.snapshot()); this.doc = JSON.parse(this.redoStack.pop()); this.dirty = true; this._pruneSel(); await this.render(); this._changed("redo"); }
  _pruneSel() { for (const id of [...this.sel]) if (!this.find(id)) this.sel.delete(id); }
  _changed(label) { this.dispatchEvent(new CustomEvent("change", { detail: label })); }
  _selChanged() { this.drawOverlay(); this.dispatchEvent(new Event("selection")); }

  select(ids, add = false) {
    if (!add) this.sel.clear();
    for (const id of [].concat(ids)) if (id) this.sel.add(id);
    this._selChanged();
  }

  // ------------------------------------------------------------------ coordinates
  toDoc(e) {
    const r = this.overlay.getBoundingClientRect();
    return { x: (e.clientX - r.left) / this.zoom, y: (e.clientY - r.top) / this.zoom };
  }
  snapV(v) { return this.snap ? Math.round(v / this.grid) * this.grid : Math.round(v); }

  hit(pt) {
    // topmost unlocked object under point (uses bounding boxes, rotation aware enough for editing)
    for (let i = this.objects.length - 1; i >= 0; i--) {
      const o = this.objects[i];
      if (o.hidden) continue;
      const b = bbox(o);
      const tol = 4 / this.zoom + (o.points ? (o.sw || 2) / 2 : 0);
      if (pt.x >= b.x - tol && pt.x <= b.x + b.w + tol && pt.y >= b.y - tol && pt.y <= b.y + b.h + tol) {
        if (o.points && TYPES[o.type]?.poly && !(o.fill && o.fill !== "none") && !this._nearPoly(o, pt, tol + 3)) continue;
        return o;
      }
    }
    return null;
  }
  _nearPoly(o, p, tol) {
    const pts = o.points;
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
      const dx = x2 - x1, dy = y2 - y1, L = dx * dx + dy * dy || 1;
      const t = clamp(((p.x - x1) * dx + (p.y - y1) * dy) / L, 0, 1);
      if (Math.hypot(p.x - (x1 + t * dx), p.y - (y1 + t * dy)) <= tol) return true;
    }
    return false;
  }

  selBox() {
    const s = this.selected;
    if (!s.length) return null;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const o of s) { const b = bbox(o); x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h); }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  // ------------------------------------------------------------------ overlay
  drawOverlay(guides = []) {
    if (!this.doc) return;
    const ov = this.overlay;
    ov.innerHTML = "";
    const z = this.zoom;
    if (this.showGrid && this.grid * z >= 5) {
      const p = svg("pattern", { id: "ed-grid", width: this.grid, height: this.grid, patternUnits: "userSpaceOnUse" });
      p.append(svg("path", { d: `M ${this.grid} 0 L 0 0 0 ${this.grid}`, fill: "none", stroke: "rgba(255,255,255,.07)", "stroke-width": 1 / z }));
      const defs = svg("defs"); defs.append(p); ov.append(defs);
      ov.append(svg("rect", { x: 0, y: 0, width: this.W, height: this.H, fill: "url(#ed-grid)", "pointer-events": "none" }));
    }
    ov.append(svg("rect", { x: 0, y: 0, width: this.W, height: this.H, fill: "none", stroke: "rgba(255,255,255,.25)", "stroke-width": 1 / z }));
    const sw = 1 / z;
    for (const o of this.selected) {
      const b = bbox(o);
      ov.append(svg("rect", { x: b.x, y: b.y, width: b.w, height: b.h, fill: "none", stroke: o.locked ? "#ff9f43" : "#3fa7ff", "stroke-width": sw, "stroke-dasharray": `${4 / z} ${3 / z}`, transform: o.rot ? `rotate(${o.rot} ${b.x + b.w / 2} ${b.y + b.h / 2})` : null }));
    }
    const box = this.selBox();
    if (box && !this.selected.every((o) => o.locked)) {
      const one = this.selected.length === 1 ? this.selected[0] : null;
      if (one && one.points && TYPES[one.type]?.poly) {
        one.points.forEach(([x, y], i) => ov.append(svg("rect", { class: "ed-vertex", "data-vertex": i, x: x - 4 / z, y: y - 4 / z, width: 8 / z, height: 8 / z, fill: "#fff", stroke: "#3fa7ff", "stroke-width": sw })));
      } else {
        ov.append(svg("rect", { x: box.x, y: box.y, width: box.w, height: box.h, fill: "none", stroke: "#3fa7ff", "stroke-width": sw, "pointer-events": "none" }));
        const hs = 7 / z;
        const pos = { nw: [0, 0], n: [0.5, 0], ne: [1, 0], e: [1, 0.5], se: [1, 1], s: [0.5, 1], sw: [0, 1], w: [0, 0.5] };
        for (const h of HANDLES) {
          const [fx, fy] = pos[h];
          ov.append(svg("rect", { class: "ed-handle", "data-handle": h, x: box.x + box.w * fx - hs / 2, y: box.y + box.h * fy - hs / 2, width: hs, height: hs, fill: "#fff", stroke: "#3fa7ff", "stroke-width": sw, style: `cursor:${h}-resize` }));
        }
        if (one) {
          const cx = box.x + box.w / 2;
          ov.append(svg("line", { x1: cx, y1: box.y, x2: cx, y2: box.y - 22 / z, stroke: "#3fa7ff", "stroke-width": sw }));
          ov.append(svg("circle", { class: "ed-handle", "data-handle": "rot", cx, cy: box.y - 22 / z, r: 5 / z, fill: "#3fa7ff", style: "cursor:grab" }));
        }
      }
    }
    for (const g of guides) ov.append(svg("line", { ...g, stroke: "#ff3fa4", "stroke-width": sw, "stroke-dasharray": `${3 / z} ${2 / z}` }));
    if (this._marquee) { const m = this._marquee; ov.append(svg("rect", { x: Math.min(m.x0, m.x1), y: Math.min(m.y0, m.y1), width: Math.abs(m.x1 - m.x0), height: Math.abs(m.y1 - m.y0), fill: "rgba(63,167,255,.12)", stroke: "#3fa7ff", "stroke-width": sw })); }
    if (this._draft) ov.append(this._draft);
  }

  // smart guides: snap moving box edges/centers to other objects
  _smart(box, excludeIds) {
    if (!this.guides) return { dx: 0, dy: 0, lines: [] };
    const tol = SNAP_PX / this.zoom;
    const xs = [box.x, box.x + box.w / 2, box.x + box.w], ys = [box.y, box.y + box.h / 2, box.y + box.h];
    let best = { dx: null, dy: null, gx: null, gy: null };
    const cands = this.objects.filter((o) => !excludeIds.has(o.id) && !o.hidden).map(bbox);
    cands.push({ x: 0, y: 0, w: this.W, h: this.H });
    for (const b of cands) {
      const tx = [b.x, b.x + b.w / 2, b.x + b.w], ty = [b.y, b.y + b.h / 2, b.y + b.h];
      for (const a of xs) for (const t of tx) { const d = t - a; if (Math.abs(d) <= tol && (best.dx === null || Math.abs(d) < Math.abs(best.dx))) { best.dx = d; best.gx = t; } }
      for (const a of ys) for (const t of ty) { const d = t - a; if (Math.abs(d) <= tol && (best.dy === null || Math.abs(d) < Math.abs(best.dy))) { best.dy = d; best.gy = t; } }
    }
    const lines = [];
    if (best.gx !== null) lines.push({ x1: best.gx, x2: best.gx, y1: 0, y2: this.H });
    if (best.gy !== null) lines.push({ y1: best.gy, y2: best.gy, x1: 0, x2: this.W });
    return { dx: best.dx ?? null, dy: best.dy ?? null, lines };
  }

  /** re-render only the edited objects while dragging (falls back to full render) */
  _live(objs) { if (!this.view.refreshObjects(objs)) this.view.setDoc(this.doc); }

  // ------------------------------------------------------------------ live drag preview (no re-render)
  _previewTransform(ids, t) { for (const id of ids) { const n = this.view.nodes.get(id); if (n) n.parts.g.setAttribute("transform", t + (n.o.rot ? ` rotate(${n.o.rot} ${bbox(n.o).x + bbox(n.o).w / 2} ${bbox(n.o).y + bbox(n.o).h / 2})` : "")); } }

  // ------------------------------------------------------------------ events
  _bindEvents() {
    const sc = this.scroller;
    sc.addEventListener("wheel", (e) => {
      if (e.ctrlKey) { e.preventDefault(); const r = sc.getBoundingClientRect(); this.setZoom(this.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left, e.clientY - r.top); }
    }, { passive: false });
    sc.addEventListener("pointerdown", (e) => this._down(e));
    sc.addEventListener("dblclick", (e) => this._dbl(e));
    sc.addEventListener("contextmenu", (e) => { e.preventDefault(); this._context(e); });
    sc.addEventListener("pointermove", (e) => { if (!this._drag) this.dispatchEvent(new CustomEvent("cursor", { detail: this.toDoc(e) })); });
    sc.addEventListener("dragover", (e) => { e.preventDefault(); });
    sc.addEventListener("drop", (e) => this._drop(e));
    sc.addEventListener("keydown", (e) => this._key(e));
  }

  _down(e) {
    this.scroller.focus({ preventScroll: true });
    if (e.button === 1 || (e.button === 0 && this._space)) { this._pan(e); return; }
    if (e.button !== 0) return;
    const pt = this.toDoc(e);
    const handle = e.target.dataset?.handle, vertex = e.target.dataset?.vertex;
    if (this.tool) { this._create(e, pt); return; }
    if (vertex !== undefined) { this._dragVertex(e, +vertex); return; }
    if (handle === "rot") { this._rotate(e, pt); return; }
    if (handle) { this._resize(e, pt, handle); return; }
    const o = this.hit(pt);
    if (!o) { if (!e.shiftKey) this.select([]); this._marqueeDrag(e, pt); return; }
    if (e.shiftKey || e.ctrlKey) { if (this.sel.has(o.id)) { this.sel.delete(o.id); this._selChanged(); } else this.select(o.id, true); return; }
    if (!this.sel.has(o.id)) this.select(o.id);
    if (o.locked) return;
    this._move(e, pt);
  }

  _capture(e, move, up) {
    const tgt = this.scroller;
    tgt.setPointerCapture(e.pointerId);
    let raf = 0, last;
    const mv = (ev) => { last = ev; if (!raf) raf = requestAnimationFrame(() => { raf = 0; move(last); }); };
    const done = (ev) => { cancelAnimationFrame(raf); tgt.removeEventListener("pointermove", mv); tgt.removeEventListener("pointerup", done); this._drag = false; up(ev); };
    this._drag = true;
    tgt.addEventListener("pointermove", mv);
    tgt.addEventListener("pointerup", done);
  }

  _pan(e) {
    const sc = this.scroller, x0 = e.clientX, y0 = e.clientY, l = sc.scrollLeft, t = sc.scrollTop;
    sc.style.cursor = "grabbing";
    this._capture(e, (ev) => { sc.scrollLeft = l - (ev.clientX - x0); sc.scrollTop = t - (ev.clientY - y0); }, () => { sc.style.cursor = ""; });
  }

  _move(e, p0) {
    let ids = new Set(this.sel);
    const box0 = this.selBox();
    let dup = e.altKey, moved = false, dx = 0, dy = 0;
    this.begin();
    this._capture(e, (ev) => {
      const p = this.toDoc(ev);
      dx = p.x - p0.x; dy = p.y - p0.y;
      if (!moved && Math.hypot(dx, dy) * this.zoom < 3) return;
      if (!moved && dup) {   // alt-drag duplicates
        const copies = this.selected.map((o) => { const c = deep(o); c.id = uid(c.type[0]); this._ensureIds([c]); return c; });
        this.objects.push(...copies);
        this.sel = new Set(copies.map((c) => c.id));
        ids = new Set(this.sel);
        this.view.setDoc(this.doc);
      }
      moved = true;
      let nx = box0.x + dx, ny = box0.y + dy;
      if (this.snap && !ev.ctrlKey) { nx = this.snapV(nx); ny = this.snapV(ny); }
      let lines = [];
      if (!ev.ctrlKey) {
        const s = this._smart({ ...box0, x: nx, y: ny }, ids);
        if (s.dx !== null) nx += s.dx;
        if (s.dy !== null) ny += s.dy;
        lines = s.lines;
      }
      if (ev.shiftKey) { if (Math.abs(nx - box0.x) > Math.abs(ny - box0.y)) ny = box0.y; else nx = box0.x; }
      dx = nx - box0.x; dy = ny - box0.y;
      this._previewTransform(ids, `translate(${dx} ${dy})`);
      this._showDragBox({ ...box0, x: nx, y: ny }, lines);
      this.dispatchEvent(new CustomEvent("cursor", { detail: { x: nx, y: ny, w: box0.w, h: box0.h } }));
    }, async () => {
      if (!moved) { this._before = null; return; }
      for (const o of this.selected) moveObj(o, dx, dy);
      await this.commit("move");
    });
  }

  _showDragBox(b, lines) {
    this.overlay.querySelectorAll(".ed-handle,.ed-vertex").forEach((h) => h.remove());
    this.drawOverlay(lines);
    this.overlay.querySelectorAll(".ed-handle,.ed-vertex").forEach((h) => h.remove());
    this.overlay.append(svg("rect", { x: b.x, y: b.y, width: b.w, height: b.h, fill: "none", stroke: "#3fa7ff", "stroke-width": 1 / this.zoom }));
  }

  _resize(e, p0, h) {
    const box0 = this.selBox();
    const objs0 = this.selected.map((o) => [o, deep(o)]);
    this.begin();
    let nb = box0;
    this._capture(e, (ev) => {
      const p = this.toDoc(ev);
      let { x, y, w, h: hh } = box0;
      let x1 = x + w, y1 = y + hh;
      const sx = this.snap && !ev.ctrlKey ? this.snapV(p.x) : p.x, sy = this.snap && !ev.ctrlKey ? this.snapV(p.y) : p.y;
      if (h.includes("w")) x = Math.min(sx, x1 - 1);
      if (h.includes("e")) x1 = Math.max(sx, x + 1);
      if (h.includes("n")) y = Math.min(sy, y1 - 1);
      if (h.includes("s")) y1 = Math.max(sy, y + 1);
      w = x1 - x; hh = y1 - y;
      if (ev.shiftKey && box0.w && box0.h) {   // keep aspect ratio
        const ar = box0.w / box0.h;
        if (h === "n" || h === "s") w = hh * ar; else hh = w / ar;
        if (h.includes("w")) x = x1 - w;
        if (h.includes("n")) y = y1 - hh;
      }
      nb = { x, y, w, h: hh };
      for (const [o, orig] of objs0) { Object.assign(o, deep(orig)); resizeObj(o, box0, nb); }
      this._live(objs0.map((x) => x[0]));
      this._showDragBox(nb, []);
      this.dispatchEvent(new CustomEvent("cursor", { detail: nb }));
    }, async () => { await this.commit("resize"); });
  }

  _rotate(e) {
    const o = this.selected[0];
    const b = bbox(o), cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    this.begin();
    this._capture(e, (ev) => {
      const p = this.toDoc(ev);
      let a = (Math.atan2(p.y - cy, p.x - cx) * 180) / Math.PI + 90;
      if (ev.shiftKey || this.snap) a = Math.round(a / 15) * 15;
      o.rot = ((Math.round(a) % 360) + 360) % 360;
      if (o.rot === 0) delete o.rot;
      this._previewTransform([o.id], "");
      const n = this.view.nodes.get(o.id);
      if (n) n.parts.g.setAttribute("transform", o.rot ? `rotate(${o.rot} ${cx} ${cy})` : "");
      this.drawOverlay();
      this.dispatchEvent(new CustomEvent("cursor", { detail: { rot: o.rot || 0 } }));
    }, async () => { await this.commit("rotate"); });
  }

  _dragVertex(e, i) {
    const o = this.selected[0];
    this.begin();
    if (e.altKey && o.points.length > 2) { o.points.splice(i, 1); this.commit("delete point"); return; }
    this._capture(e, (ev) => {
      const p = this.toDoc(ev);
      let x = this.snap && !ev.ctrlKey ? this.snapV(p.x) : Math.round(p.x), y = this.snap && !ev.ctrlKey ? this.snapV(p.y) : Math.round(p.y);
      if (ev.shiftKey && o.points.length > 1) {   // orthogonal to previous point
        const [px, py] = o.points[i === 0 ? 1 : i - 1];
        if (Math.abs(x - px) > Math.abs(y - py)) y = py; else x = px;
      }
      o.points[i] = [x, y];
      this._live([o]);
      this.drawOverlay();
    }, async () => { await this.commit("edit points"); });
  }

  _marqueeDrag(e, p0) {
    this._marquee = { x0: p0.x, y0: p0.y, x1: p0.x, y1: p0.y };
    const base = new Set(this.sel);
    this._capture(e, (ev) => {
      const p = this.toDoc(ev);
      Object.assign(this._marquee, { x1: p.x, y1: p.y });
      const m = this._marquee, x0 = Math.min(m.x0, m.x1), x1 = Math.max(m.x0, m.x1), y0 = Math.min(m.y0, m.y1), y1 = Math.max(m.y0, m.y1);
      const touch = ev.altKey;   // alt: select touched, default: fully enclosed
      this.sel = new Set(base);
      for (const o of this.objects) {
        if (o.hidden) continue;
        const b = bbox(o);
        const inside = touch ? b.x < x1 && b.x + b.w > x0 && b.y < y1 && b.y + b.h > y0 : b.x >= x0 && b.x + b.w <= x1 && b.y >= y0 && b.y + b.h <= y1;
        if (inside) this.sel.add(o.id);
      }
      this.drawOverlay();
    }, () => { this._marquee = null; this._selChanged(); });
  }

  // ------------------------------------------------------------------ creation tools
  setTool(type) { this.tool = type; this.scroller.style.cursor = type ? "crosshair" : ""; this.dispatchEvent(new Event("tool")); }

  _create(e, p0) {
    const type = this.tool;
    const T = TYPES[type];
    const sx = this.snapV(p0.x), sy = this.snapV(p0.y);
    if (T.poly && type !== "line" && type !== "pipe" || type === "polygon") return this._createPoly(e, type, sx, sy);
    const o = { id: uid(type[0]), type, ...deep(T.defaults) };
    if (T.poly) {
      o.points = [[sx, sy], [sx, sy]];
      this.begin();
      this.objects.push(o);
      this._capture(e, (ev) => {
        const p = this.toDoc(ev);
        let x = this.snapV(p.x), y = this.snapV(p.y);
        if (ev.shiftKey || type === "pipe") { if (Math.abs(x - sx) > Math.abs(y - sy)) y = sy; else x = sx; }
        o.points[1] = [x, y];
        this.view.setDoc(this.doc);
      }, async () => {
        if (o.points[0][0] === o.points[1][0] && o.points[0][1] === o.points[1][1]) o.points[1] = [sx + 120, sy];
        this.sel = new Set([o.id]);
        if (!e.shiftKey) this.setTool(null);
        await this.commit("create");
        this._selChanged();
      });
      return;
    }
    o.x = sx; o.y = sy;
    const dw = o.w, dh = o.h;
    this.begin();
    this.objects.push(o);
    let dragged = false;
    this._capture(e, (ev) => {
      const p = this.toDoc(ev);
      let w = this.snapV(p.x) - sx, h = this.snapV(p.y) - sy;
      if (Math.abs(w) < 3 && Math.abs(h) < 3) return;
      dragged = true;
      if (ev.shiftKey) { const m = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * m; h = Math.sign(h || 1) * m; }
      o.x = Math.min(sx, sx + w); o.y = Math.min(sy, sy + h); o.w = Math.max(4, Math.abs(w)); o.h = Math.max(4, Math.abs(h));
      this.view.setDoc(this.doc);
      this._showDragBox(o, []);
    }, async () => {
      if (!dragged) { o.w = dw; o.h = dh; }
      this.sel = new Set([o.id]);
      if (!e.shiftKey) this.setTool(null);   // shift keeps the tool active
      await this.commit("create");
      this._selChanged();
    });
  }

  _createPoly(e, type, sx, sy) {
    if (!this._poly) {
      this._poly = { id: uid(type[0]), type, ...deep(TYPES[type].defaults), points: [[sx, sy], [sx, sy]] };
      this.begin();
      this.objects.push(this._poly);
      const move = (ev) => {
        if (!this._poly) return;
        const p = this.toDoc(ev);
        let x = this.snapV(p.x), y = this.snapV(p.y);
        const pts = this._poly.points, [px, py] = pts[pts.length - 2];
        if (ev.shiftKey) { if (Math.abs(x - px) > Math.abs(y - py)) y = py; else x = px; }
        pts[pts.length - 1] = [x, y];
        this.view.setDoc(this.doc);
      };
      this._polyMove = move;
      this.scroller.addEventListener("pointermove", move);
      toast("Click to add points, double-click or Enter to finish, Esc to cancel", "info", 2500);
    } else {
      const pts = this._poly.points;
      pts.push([...pts[pts.length - 1]]);
    }
  }

  async _finishPoly(cancel = false) {
    const p = this._poly;
    if (!p) return;
    this.scroller.removeEventListener("pointermove", this._polyMove);
    this._poly = null;
    p.points.pop();
    if (cancel || p.points.length < 2) { this.doc.objects = this.objects.filter((o) => o !== p); this._before = null; await this.render(); }
    else { this.sel = new Set([p.id]); await this.commit("create"); }
    this.setTool(null);
    this._selChanged();
  }

  async _dbl(e) {
    if (this._poly) { await this._finishPoly(); return; }
    const pt = this.toDoc(e);
    const o = this.hit(pt);
    if (!o) return;
    if (o.points && TYPES[o.type]?.poly && e.ctrlKey) {   // ctrl+dblclick on a segment inserts a vertex
      let best = 0, bd = Infinity;
      for (let i = 0; i < o.points.length - 1; i++) { const [x1, y1] = o.points[i], [x2, y2] = o.points[i + 1]; const d = Math.hypot((x1 + x2) / 2 - pt.x, (y1 + y2) / 2 - pt.y); if (d < bd) { bd = d; best = i; } }
      await this.edit(() => o.points.splice(best + 1, 0, [Math.round(pt.x), Math.round(pt.y)]), "insert point");
      return;
    }
    if ("text" in o) {
      const t = await prompt("Text", o.text);
      if (t !== null) await this.edit(() => (o.text = t), "text");
    } else if (o.type === "symbol" && o.src) {
      this.app.openInEditor(o.src);
    } else this.dispatchEvent(new CustomEvent("focus-props"));
  }

  // ------------------------------------------------------------------ drop (tags from browser, symbols, images)
  async _drop(e) {
    e.preventDefault();
    const pt = this.toDoc(e);
    const data = e.dataTransfer.getData("application/x-gf");
    if (!data) return;
    const d = JSON.parse(data);
    const target = this.hit(pt);
    await this.edit(() => {
      if (d.kind === "tag") {
        if (target && !e.shiftKey) {   // bind dropped tag to the object under the cursor
          if ("expr" in target) target.expr = `{{${d.ref}}}`;
          else if ("tag" in target) target.tag = d.ref;
          else if (target.type === "trend") target.pens.push({ expr: `{{${d.ref}}}`, name: d.name || d.ref });
          else if (target.type === "histtrend" && d.ref.startsWith("hist:")) target.pens.push({ desc: d.ref.slice(5), name: d.name });
          else { target.dyn = target.dyn || {}; target.dyn.fill = { expr: `{{${d.ref}}}`, type: "bool", on: "#2ee66b", off: "#59616c" }; }
          this.sel = new Set([target.id]);
          toast(`bound ${d.ref} to ${target.id}`);
        } else {
          const lbl = { id: uid("t"), type: "text", ...deep(TYPES.text.defaults), x: this.snapV(pt.x), y: this.snapV(pt.y), w: 160, text: d.name || d.ref, fs: 14 };
          const v = { id: uid("v"), type: "value", ...deep(TYPES.value.defaults), x: this.snapV(pt.x) + 165, y: this.snapV(pt.y), expr: `{{${d.ref}}}`, units: d.units || "" };
          this.objects.push(lbl, v);
          this.sel = new Set([lbl.id, v.id]);
        }
      } else if (d.kind === "symbol") {
        const o = { id: uid("s"), type: "symbol", x: this.snapV(pt.x), y: this.snapV(pt.y), w: d.w || 100, h: d.h || 100, src: d.path, aliases: d.aliases || {} };
        this.objects.push(o);
        this.sel = new Set([o.id]);
      } else if (d.kind === "image") {
        const o = { id: uid("i"), type: "image", ...deep(TYPES.image.defaults), x: this.snapV(pt.x), y: this.snapV(pt.y), src: d.path };
        this.objects.push(o); this.sel = new Set([o.id]);
      } else if (d.kind === "tool") {
        const T = TYPES[d.type];
        const o = { id: uid(d.type[0]), type: d.type, ...deep(T.defaults) };
        if (T.poly) moveObj(o, this.snapV(pt.x), this.snapV(pt.y)); else { o.x = this.snapV(pt.x); o.y = this.snapV(pt.y); }
        this.objects.push(o); this.sel = new Set([o.id]);
      }
    }, "drop");
    this._selChanged();
  }

  // ------------------------------------------------------------------ keyboard
  async _key(e) {
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    if (e.target !== this.scroller) return;
    if (k === " ") { this._space = true; this.scroller.style.cursor = "grab"; this.scroller.addEventListener("keyup", () => { this._space = false; this.scroller.style.cursor = this.tool ? "crosshair" : ""; }, { once: true }); e.preventDefault(); return; }
    if (this._poly && (k === "Enter" || k === "Escape")) { e.preventDefault(); await this._finishPoly(k === "Escape"); return; }
    if (k === "Escape") { this.setTool(null); this.select([]); return; }
    if (ctrl && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? await this.redo() : await this.undo(); return; }
    if (ctrl && k.toLowerCase() === "y") { e.preventDefault(); await this.redo(); return; }
    if (ctrl && k.toLowerCase() === "a") { e.preventDefault(); this.select(this.objects.filter((o) => !o.hidden).map((o) => o.id)); return; }
    if (ctrl && k.toLowerCase() === "c") { e.preventDefault(); this.copy(); return; }
    if (ctrl && k.toLowerCase() === "x") { e.preventDefault(); this.copy(); await this.remove(); return; }
    if (ctrl && k.toLowerCase() === "v") { e.preventDefault(); await this.paste(); return; }
    if (ctrl && k.toLowerCase() === "d") { e.preventDefault(); await this.duplicate(); return; }
    if (ctrl && k.toLowerCase() === "g") { e.preventDefault(); e.shiftKey ? await this.ungroup() : await this.group(); return; }
    if (ctrl && k.toLowerCase() === "l") { e.preventDefault(); await this.toggleLock(); return; }
    if (ctrl && (k === "]" || k === "[")) { e.preventDefault(); await this.zorder(k === "]" ? (e.shiftKey ? "front" : "forward") : (e.shiftKey ? "back" : "backward")); return; }
    if (ctrl && (k === "=" || k === "+")) { e.preventDefault(); this.setZoom(this.zoom * 1.25); return; }
    if (ctrl && k === "-") { e.preventDefault(); this.setZoom(this.zoom / 1.25); return; }
    if (ctrl && k === "0") { e.preventDefault(); this.fit(); return; }
    if (ctrl && k === "1") { e.preventDefault(); this.setZoom(1); return; }
    if (k === "Delete" || k === "Backspace") { e.preventDefault(); await this.remove(); return; }
    if (k === "Tab") { e.preventDefault(); const ids = this.objects.map((o) => o.id); const cur = ids.indexOf([...this.sel][0]); this.select(ids[(cur + (e.shiftKey ? -1 : 1) + ids.length) % ids.length]); return; }
    if (k.startsWith("Arrow") && this.sel.size) {
      e.preventDefault();
      const step = e.shiftKey ? (this.grid || 10) : 1;
      const dx = k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0, dy = k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0;
      clearTimeout(this._nudgeT);
      if (!this._nudging) { this.begin(); this._nudging = true; }
      for (const o of this.selected) if (!o.locked) moveObj(o, dx, dy);
      await this.view.setDoc(this.doc); this.drawOverlay();
      this._nudgeT = setTimeout(() => { this._nudging = false; this.commit("nudge", false); }, 400);
      return;
    }
    if (!ctrl && k.toLowerCase() === "g" && !e.altKey) { this.snap = !this.snap; toast(`snap to grid ${this.snap ? "on" : "off"}`); this._changed("snap"); }
  }

  // ------------------------------------------------------------------ operations
  copy() {
    const items = this.selected.map(deep);
    if (!items.length) return;
    this.clipboard = items;
    const text = JSON.stringify({ format: "noelle-clipboard", objects: items }, null, 2);
    navigator.clipboard?.writeText(text).catch(() => {});
    toast(`${items.length} object(s) copied (also as JSON text)`);
  }

  async paste() {
    let items = this.clipboard;
    try {
      const t = await navigator.clipboard.readText();
      const j = JSON.parse(t);
      if (j.format === "noelle-clipboard") items = j.objects;
      else if (j.type) items = [j];
      else if (Array.isArray(j) && j[0]?.type) items = j;
    } catch { /* not JSON / no permission -> internal clipboard */ }
    if (!items || !items.length) return;
    await this.edit(() => {
      const copies = deep(items);
      copies.forEach((c) => { c.id = null; moveObj(c, this.grid || 10, this.grid || 10); });
      this._ensureIds(copies);
      this.objects.push(...copies);
      this.sel = new Set(copies.map((c) => c.id));
      this.clipboard = copies;
    }, "paste");
    this._selChanged();
  }

  async duplicate() { this.copy(); await this.paste(); }

  async remove() {
    if (!this.sel.size) return;
    await this.edit(() => { this.doc.objects = this.objects.filter((o) => !this.sel.has(o.id) || o.locked); this._pruneSel(); }, "delete");
    this._selChanged();
  }

  async group() {
    const s = this.selected;
    if (s.length < 2) return;
    await this.edit(() => {
      const idx = Math.max(...s.map((o) => this.objects.indexOf(o)));
      const g = { id: uid("g"), type: "group", children: s };
      const rest = this.objects.filter((o) => !this.sel.has(o.id));
      rest.splice(idx - s.length + 1, 0, g);
      this.doc.objects = rest;
      this.sel = new Set([g.id]);
    }, "group");
    this._selChanged();
  }

  async ungroup() {
    const gs = this.selected.filter((o) => o.type === "group");
    if (!gs.length) return;
    await this.edit(() => {
      const out = [], ns = new Set();
      for (const o of this.objects) {
        if (gs.includes(o)) { for (const c of o.children) { if (o.opacity != null && c.opacity == null) c.opacity = o.opacity; out.push(c); ns.add(c.id); } }
        else out.push(o);
      }
      this.doc.objects = out; this.sel = ns;
    }, "ungroup");
    this._selChanged();
  }

  async zorder(how) {
    if (!this.sel.size) return;
    await this.edit(() => {
      let a = this.objects;
      const s = a.filter((o) => this.sel.has(o.id)), r = a.filter((o) => !this.sel.has(o.id));
      if (how === "front") a = [...r, ...s];
      else if (how === "back") a = [...s, ...r];
      else {
        a = [...a];
        const dir = how === "forward" ? 1 : -1;
        const order = dir > 0 ? [...a.keys()].reverse() : [...a.keys()];
        for (const i of order) { const j = i + dir; if (this.sel.has(a[i].id) && j >= 0 && j < a.length && !this.sel.has(a[j].id)) [a[i], a[j]] = [a[j], a[i]]; }
      }
      this.doc.objects = a;
    }, "z-order");
  }

  async toggleLock() { const lock = !this.selected.every((o) => o.locked); await this.edit(() => this.selected.forEach((o) => { if (lock) o.locked = true; else delete o.locked; }), "lock"); }
  async toggleHide() { await this.edit(() => this.selected.forEach((o) => { o.hidden = !o.hidden; if (!o.hidden) delete o.hidden; }), "hide"); }

  async align(how) {
    const s = this.selected;
    if (!s.length) return;
    const ref = s.length === 1 ? { x: 0, y: 0, w: this.W, h: this.H } : this.selBox();
    await this.edit(() => {
      for (const o of s) {
        const b = bbox(o);
        let dx = 0, dy = 0;
        if (how === "left") dx = ref.x - b.x;
        if (how === "right") dx = ref.x + ref.w - (b.x + b.w);
        if (how === "hcenter") dx = ref.x + ref.w / 2 - (b.x + b.w / 2);
        if (how === "top") dy = ref.y - b.y;
        if (how === "bottom") dy = ref.y + ref.h - (b.y + b.h);
        if (how === "vcenter") dy = ref.y + ref.h / 2 - (b.y + b.h / 2);
        moveObj(o, Math.round(dx), Math.round(dy));
      }
    }, "align");
  }

  async distribute(axis) {
    const s = this.selected.map((o) => [o, bbox(o)]);
    if (s.length < 3) { toast("select 3 or more objects"); return; }
    await this.edit(() => {
      const k = axis === "h" ? "x" : "y", d = axis === "h" ? "w" : "h";
      s.sort((a, b) => a[1][k] - b[1][k]);
      const first = s[0][1], last = s[s.length - 1][1];
      const total = s.reduce((t, [, b]) => t + b[d], 0);
      const gap = (last[k] + last[d] - first[k] - total) / (s.length - 1);
      let pos = first[k];
      for (const [o, b] of s) { const delta = Math.round(pos - b[k]); axis === "h" ? moveObj(o, delta, 0) : moveObj(o, 0, delta); pos += b[d] + gap; }
    }, "distribute");
  }

  async sameSize(which) {
    const s = this.selected;
    if (s.length < 2) return;
    const ref = bbox(s[0]);
    await this.edit(() => s.slice(1).forEach((o) => { const b = bbox(o); resizeObj(o, b, { x: b.x, y: b.y, w: which !== "h" ? ref.w : b.w, h: which !== "w" ? ref.h : b.h }); }), "same size");
  }

  async flip(axis) {
    await this.edit(() => {
      const box = this.selBox();
      for (const o of this.selected) {
        const b = bbox(o);
        if (o.points) o.points = o.points.map(([x, y]) => axis === "h" ? [box.x + box.x + box.w - x, y] : [x, box.y + box.y + box.h - y]);
        else if (axis === "h") moveObj(o, box.x + box.x + box.w - (b.x + b.w) - b.x, 0);
        else moveObj(o, 0, box.y + box.y + box.h - (b.y + b.h) - b.y);
      }
    }, "flip");
  }

  async findReplace() {
    const f = el("input", { placeholder: "find (text, tag, alias)" }), r = el("input", { placeholder: "replace with" });
    const scope = el("select", {}, el("option", { value: "all" }, "Whole display"), el("option", { value: "sel" }, "Selection only"));
    const res = await modal({ title: "Find & replace", body: el("div", { class: "form" }, el("label", {}, "Find"), f, el("label", {}, "Replace"), r, el("label", {}, "Scope"), scope, el("small", {}, "Replaces inside every property: tag references, expressions, texts, alias values. Tip: replace 'Pump1' with '<#Pump#>' to turn a screen into a symbol.")), buttons: [["Replace all", () => [f.value, r.value, scope.value], "primary"], ["Cancel", null]] });
    if (!res || !res[0]) return;
    const [a, b, sc] = res;
    let n = 0;
    const walk = (v) => {
      if (typeof v === "string") { if (v.includes(a)) { n += v.split(a).length - 1; return v.split(a).join(b); } return v; }
      if (Array.isArray(v)) return v.map(walk);
      if (v && typeof v === "object") { for (const k of Object.keys(v)) if (k !== "id" && k !== "type") v[k] = walk(v[k]); return v; }
      return v;
    };
    await this.edit(() => { const list = sc === "sel" ? this.selected : this.objects; list.forEach(walk); }, "replace");
    toast(`${n} replacement(s)`);
  }

  async makeSymbol() {
    const s = this.selected;
    if (!s.length) { toast("select objects first"); return; }
    const name = await prompt("Symbol name", "MySymbol");
    if (!name) return;
    const box = this.selBox();
    const objs = deep(s);
    objs.forEach((o) => moveObj(o, -box.x, -box.y));
    const text = JSON.stringify(objs);
    const aliases = {};
    for (const m of text.matchAll(/<#([^#<>]+)#>/g)) aliases[m[1]] = "";
    const sdoc = { format: "noelle-display", version: 1, name, size: [Math.ceil(box.w), Math.ceil(box.h)], background: "transparent", aliases, objects: objs };
    const path = `symbols/${name}.nll`;
    await api.put(`/api/file?path=${encodeURIComponent(path)}`, JSON.stringify(sdoc, null, 2), true);
    if (await confirmBox(`Saved ${path}. Replace the selection with an instance of the symbol?`)) {
      await this.edit(() => {
        const idx = this.objects.indexOf(s[0]);
        this.doc.objects = this.objects.filter((o) => !this.sel.has(o.id));
        const inst = { id: uid("s"), type: "symbol", x: Math.round(box.x), y: Math.round(box.y), w: Math.ceil(box.w), h: Math.ceil(box.h), src: path, aliases };
        this.doc.objects.splice(Math.max(0, idx), 0, inst);
        this.sel = new Set([inst.id]);
      }, "make symbol");
    }
    this.app.refreshProject();
  }

  _context(e) {
    const pt = this.toDoc(e);
    const o = this.hit(pt);
    if (o && !this.sel.has(o.id)) this.select(o.id);
    const items = [
      ["Cut", "Ctrl+X", () => { this.copy(); this.remove(); }], ["Copy", "Ctrl+C", () => this.copy()], ["Paste", "Ctrl+V", () => this.paste()], ["Duplicate", "Ctrl+D", () => this.duplicate()], ["Delete", "Del", () => this.remove()], null,
      ["Bring to front", "Ctrl+Shift+]", () => this.zorder("front")], ["Send to back", "Ctrl+Shift+[", () => this.zorder("back")], null,
      ["Group", "Ctrl+G", () => this.group()], ["Ungroup", "Ctrl+Shift+G", () => this.ungroup()], ["Lock / unlock", "Ctrl+L", () => this.toggleLock()], null,
      ["Make symbol…", "", () => this.makeSymbol()], ["Find & replace…", "", () => this.findReplace()],
    ];
    this.app.contextMenu(e.clientX, e.clientY, items);
  }

  // ------------------------------------------------------------------ save
  async save() {
    const text = JSON.stringify(this.doc, null, 2) + "\n";
    await api.put(`/api/file?path=${encodeURIComponent(this.path)}`, text, true);
    this.dirty = false;
    this._changed("save");
    toast(`saved ${this.path}`);
  }
}
