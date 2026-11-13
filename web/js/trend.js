// Canvas trend chart used by the real-time trend, historian trend and the historian viewer.
//
// Performance: drawing is O(points in view) with per-pixel-column min/max
// decimation (each column draws at most one vertical segment + joins), redraws
// are coalesced to one per animation frame and skipped while off-screen.
import { el } from "./util.js";

export const PALETTE = ["#3fa7ff", "#ff8c42", "#2ee66b", "#f2c14e", "#d36cff", "#ff5a7a", "#40e0d0", "#b0bec5", "#8bc34a", "#ff7043", "#7986cb", "#ffd54f"];

function niceStep(range, target) {
  const raw = range / Math.max(1, target);
  const p = 10 ** Math.floor(Math.log10(raw));
  const f = raw / p;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
}
const TIME_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800, 2592000].map((s) => s * 1000);

function lowerBound(arr, x) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return lo; }

export class Trend {
  constructor(host, opts = {}) {
    this.host = host;
    this.o = Object.assign({ bg: "#111418", grid: "#2a3038", text: "#aab2bd", legend: true, ymin: null, ymax: null, span: 300000, live: true, font: "11px Segoe UI, Arial" }, opts);
    this.pens = [];
    this.t1 = Date.now(); this.t0 = this.t1 - this.o.span;
    this.cursor = null;
    this.canvas = el("canvas", { class: "gf-trend-canvas" });
    this.legendEl = el("div", { class: "gf-trend-legend" });
    this.tip = el("div", { class: "gf-trend-tip", style: { display: "none" } });
    host.classList.add("gf-trend");
    host.append(this.canvas, this.legendEl, this.tip);
    this.ctx = this.canvas.getContext("2d");
    this.visible = true;
    this._raf = 0;
    this.onRange = null;   // (t0, t1) => void  (historian lazy load)
    this._bind();
    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(host);
    this.io = new IntersectionObserver((e) => { this.visible = e[0].isIntersecting; if (this.visible) this.draw(); });
    this.io.observe(host);
    this._resize();
  }

  destroy() { this.ro.disconnect(); this.io.disconnect(); cancelAnimationFrame(this._raf); this.host.innerHTML = ""; }

  setPens(pens) {
    this.pens = pens.map((p, i) => ({ name: p.name || `Pen ${i + 1}`, color: p.color || PALETTE[i % PALETTE.length], width: p.width || 1.6, step: !!p.step, hidden: false, t: p.t || [], v: p.v || [], units: p.units || "", fmt: p.fmt }));
    this._legend();
    this.draw();
  }

  setData(i, t, v, step) { const p = this.pens[i]; if (!p) return; p.t = t; p.v = v; if (step !== undefined) p.step = step; this.draw(); }

  append(i, t, v) {
    const p = this.pens[i];
    if (!p || typeof v !== "number" || !isFinite(v)) return;
    const n = p.t.length;
    if (n && t < p.t[n - 1]) return;
    p.t.push(t); p.v.push(v);
    // trim to 2x span to bound memory
    if (p.t.length > 4096 && p.t[0] < Date.now() - this.o.span * 2) {
      const k = lowerBound(p.t, Date.now() - this.o.span * 1.5);
      p.t.splice(0, k); p.v.splice(0, k);
    }
    this.draw();
  }

  setRange(t0, t1, fromUser = false) {
    this.t0 = t0; this.t1 = t1;
    if (fromUser) this.o.live = false;
    this.draw();
    if (this.onRange) this.onRange(t0, t1);
  }

  follow(on = true) { this.o.live = on; if (on) { const s = this.t1 - this.t0; this.t1 = Date.now(); this.t0 = this.t1 - s; } this.draw(); }

  tick() { if (this.o.live) { const s = this.t1 - this.t0; this.t1 = Date.now(); this.t0 = this.t1 - s; this.draw(); } }

  _resize() {
    const r = this.host.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(50, this.host.clientWidth), h = Math.max(40, this.host.clientHeight - (this.o.legend ? this.legendEl.offsetHeight : 0));
    this.W = w; this.H = h;
    this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = w + "px"; this.canvas.style.height = h + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    void r;
    this.draw(true);
  }

  draw(now = false) {
    if (now) { this._draw(); return; }
    if (this._raf || !this.visible) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this._draw(); });
  }

  _yRange() {
    if (this.o.ymin != null && this.o.ymin !== "" && this.o.ymax != null && this.o.ymax !== "") return [+this.o.ymin, +this.o.ymax];
    let lo = Infinity, hi = -Infinity;
    for (const p of this.pens) {
      if (p.hidden) continue;
      const a = Math.max(0, lowerBound(p.t, this.t0) - 1), b = Math.min(p.t.length, lowerBound(p.t, this.t1) + 1);
      for (let i = a; i < b; i++) { const v = p.v[i]; if (v == null) continue; if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    if (!isFinite(lo)) return [0, 1];
    if (this.o.ymin != null && this.o.ymin !== "") lo = +this.o.ymin;
    if (this.o.ymax != null && this.o.ymax !== "") hi = +this.o.ymax;
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.06;
    return [lo - pad, hi + pad];
  }

  _draw() {
    const c = this.ctx, W = this.W, H = this.H;
    if (!W || !H) return;
    const L = 54, R = 10, T = 8, B = 22;
    const pw = W - L - R, ph = H - T - B;
    this.plot = { L, T, pw, ph };
    c.fillStyle = this.o.bg; c.fillRect(0, 0, W, H);
    const [y0, y1] = this.yr = this._yRange();
    const t0 = this.t0, t1 = this.t1, span = t1 - t0 || 1;
    const X = (t) => L + ((t - t0) / span) * pw, Y = (v) => T + ph - ((v - y0) / (y1 - y0)) * ph;
    // grid
    c.font = this.o.font; c.lineWidth = 1; c.strokeStyle = this.o.grid; c.fillStyle = this.o.text;
    const ys = niceStep(y1 - y0, Math.max(2, ph / 40));
    c.textAlign = "right"; c.textBaseline = "middle";
    c.beginPath();
    for (let v = Math.ceil(y0 / ys) * ys; v <= y1; v += ys) { const y = Math.round(Y(v)) + 0.5; c.moveTo(L, y); c.lineTo(L + pw, y); c.fillText(+v.toPrecision(6) + "", L - 4, y); }
    const ts = TIME_STEPS.find((s) => (pw / (span / s)) > 80) || TIME_STEPS[TIME_STEPS.length - 1];
    const tz = new Date().getTimezoneOffset() * 60000;
    c.textAlign = "center"; c.textBaseline = "top";
    for (let t = Math.ceil((t0 - tz) / ts) * ts + tz; t <= t1; t += ts) {
      const x = Math.round(X(t)) + 0.5; c.moveTo(x, T); c.lineTo(x, T + ph);
      const d = new Date(t);
      const lbl = ts >= 86400000 ? d.toLocaleDateString() : ts >= 60000 ? d.toTimeString().slice(0, 5) : d.toTimeString().slice(0, 8);
      c.fillText(lbl, x, T + ph + 4);
    }
    c.stroke();
    c.strokeStyle = this.o.text; c.strokeRect(L + 0.5, T + 0.5, pw, ph);
    // series
    c.save(); c.beginPath(); c.rect(L, T, pw, ph); c.clip();
    for (const p of this.pens) {
      if (p.hidden || !p.t.length) continue;
      c.strokeStyle = p.color; c.lineWidth = p.width; c.lineJoin = "round";
      const a = Math.max(0, lowerBound(p.t, t0) - 1), b = Math.min(p.t.length, lowerBound(p.t, t1) + 1);
      c.beginPath();
      // per pixel column keep first/min/max/last -> at most 4 vertices per column
      let col = null, fy = 0, mn = 0, mx = 0, ly = 0, started = false;
      const emit = () => { if (col === null) return; const cx = col + 0.5; c.lineTo(cx, fy); if (mn !== mx) { c.lineTo(cx, mn); c.lineTo(cx, mx); } c.lineTo(cx, ly); };
      for (let i = a; i < b; i++) {
        const v = p.v[i];
        if (v == null) { emit(); col = null; started = false; continue; }
        const x = X(p.t[i]), y = Y(v), xc = Math.floor(x);
        if (p.step) { if (!started) { c.moveTo(x, y); started = true; } else { c.lineTo(x, ly); c.lineTo(x, y); } ly = y; continue; }
        if (!started) { c.moveTo(x, y); started = true; col = xc; fy = mn = mx = ly = y; continue; }
        if (xc === col) { if (y < mn) mn = y; if (y > mx) mx = y; ly = y; continue; }
        emit(); col = xc; fy = mn = mx = ly = y;
      }
      if (p.step && started && this.o.live) c.lineTo(X(Math.min(t1, Date.now())), ly);
      else emit();
      c.stroke();
    }
    c.restore();
    // cursor
    if (this.cursor != null) {
      const x = X(this.cursor);
      c.strokeStyle = "rgba(255,255,255,.55)"; c.setLineDash([4, 3]); c.beginPath(); c.moveTo(x + 0.5, T); c.lineTo(x + 0.5, T + ph); c.stroke(); c.setLineDash([]);
    }
    if (this._zoomBox) { const z = this._zoomBox; c.fillStyle = "rgba(63,167,255,.18)"; c.fillRect(Math.min(z.x0, z.x1), T, Math.abs(z.x1 - z.x0), ph); }
  }

  _legend() {
    this.legendEl.innerHTML = "";
    this.legendEl.style.display = this.o.legend ? "" : "none";
    this.pens.forEach((p, i) => {
      const item = el("span", { class: "gf-leg" + (p.hidden ? " off" : ""), title: "click: show/hide" }, el("i", { style: { background: p.color } }), p.name, el("b", { "data-i": i }, ""));
      item.onclick = () => { p.hidden = !p.hidden; item.classList.toggle("off", p.hidden); this.draw(); };
      this.legendEl.append(item);
    });
  }

  setLegendValues(vals) { this.legendEl.querySelectorAll("b[data-i]").forEach((b) => { const v = vals[+b.dataset.i]; b.textContent = v == null ? "" : " " + v; }); }

  _valueAt(p, t) {
    if (!p.t.length) return null;
    let i = lowerBound(p.t, t);
    if (i >= p.t.length) i = p.t.length - 1;
    if (i > 0 && (p.step || Math.abs(p.t[i - 1] - t) < Math.abs(p.t[i] - t))) i = i - (p.step && p.t[i] > t ? 1 : (p.step ? 0 : 1));
    return { t: p.t[i], v: p.v[i] };
  }

  _bind() {
    const cv = this.canvas;
    const tAt = (x) => this.t0 + ((x - this.plot.L) / this.plot.pw) * (this.t1 - this.t0);
    let drag = null;
    cv.addEventListener("wheel", (e) => {
      e.preventDefault(); e.stopPropagation();
      const k = e.deltaY > 0 ? 1.25 : 0.8;
      const tc = tAt(e.offsetX), s = (this.t1 - this.t0) * k;
      const f = (tc - this.t0) / (this.t1 - this.t0);
      this.setRange(tc - s * f, tc + s * (1 - f), true);
    }, { passive: false });
    cv.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      cv.setPointerCapture(e.pointerId);
      drag = { x: e.offsetX, t0: this.t0, t1: this.t1, zoom: e.shiftKey };
      if (drag.zoom) this._zoomBox = { x0: e.offsetX, x1: e.offsetX };
    });
    cv.addEventListener("pointermove", (e) => {
      if (drag) {
        if (drag.zoom) { this._zoomBox.x1 = e.offsetX; this.draw(); return; }
        const dt = ((e.offsetX - drag.x) / this.plot.pw) * (drag.t1 - drag.t0);
        this.setRange(drag.t0 - dt, drag.t1 - dt, true);
        return;
      }
      if (!this.plot) return;
      const t = tAt(e.offsetX);
      if (e.offsetX < this.plot.L || e.offsetX > this.plot.L + this.plot.pw) { this.cursor = null; this.tip.style.display = "none"; this.draw(); return; }
      this.cursor = t;
      const rows = this.pens.filter((p) => !p.hidden).map((p) => { const s = this._valueAt(p, t); return s ? `<div><i style="background:${p.color}"></i>${p.name}: <b>${s.v == null ? "—" : +Number(s.v).toPrecision(6)}</b> ${p.units || ""}</div>` : ""; });
      this.tip.innerHTML = `<div class="tt">${new Date(t).toLocaleString()}</div>` + rows.join("");
      this.tip.style.display = "";
      const left = e.offsetX + 14 + this.tip.offsetWidth > this.W ? e.offsetX - this.tip.offsetWidth - 10 : e.offsetX + 14;
      this.tip.style.left = left + "px"; this.tip.style.top = Math.max(0, e.offsetY - 10) + "px";
      this.draw();
    });
    const end = () => {
      if (drag && drag.zoom && this._zoomBox) {
        const a = tAt(Math.min(this._zoomBox.x0, this._zoomBox.x1)), b = tAt(Math.max(this._zoomBox.x0, this._zoomBox.x1));
        this._zoomBox = null;
        if (b - a > 1000) this.setRange(a, b, true); else this.draw();
      }
      drag = null;
    };
    cv.addEventListener("pointerup", end);
    cv.addEventListener("pointercancel", end);
    cv.addEventListener("pointerleave", () => { this.cursor = null; this.tip.style.display = "none"; this.draw(); });
    cv.addEventListener("dblclick", (e) => { e.stopPropagation(); this.follow(true); if (this.onRange) this.onRange(this.t0, this.t1); });
  }
}
