// Historian viewer (tree / search / pens / time range / zoom / stats / CSV export)
import { el, toast, modal, debounce, esc } from "./util.js";
import { api } from "./net.js";
import { Trend, PALETTE } from "./trend.js";

const RANGES = [["15m", 900], ["1h", 3600], ["8h", 28800], ["24h", 86400], ["3d", 259200], ["7d", 604800], ["30d", 2592000]];
const LS = "gf.hist.pens";

function lsGet(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } }

/** Historian tag tree with checkboxes. onToggle(desc, checked) */
function histTree(host, isChecked, onToggle) {
  host.innerHTML = "";
  const mk = (label, loadKids, desc) => {
    const row = el("div", { class: "ht-row" });
    if (desc) {
      const cb = el("input", { type: "checkbox" });
      cb.checked = isChecked(desc);
      cb.onchange = () => onToggle(desc, cb.checked);
      row.append(el("label", { class: "ht-leaf", title: desc }, cb, el("span", {}, label)));
      row.draggable = true;
      row.ondragstart = (e) => e.dataTransfer.setData("application/x-gf", JSON.stringify({ kind: "tag", ref: "hist:" + desc, name: desc }));
      return row;
    }
    const head = el("div", { class: "ht-branch" }, el("span", { class: "arrow" }, "▸"), label);
    const kids = el("div", { class: "ht-kids", style: { display: "none" } });
    let loaded = false;
    head.onclick = async () => {
      const open = kids.style.display === "none";
      kids.style.display = open ? "" : "none";
      head.firstChild.textContent = open ? "▾" : "▸";
      if (open && !loaded) {
        loaded = true; kids.textContent = "loading…";
        try { const items = await loadKids(); kids.innerHTML = ""; items.forEach((n) => kids.append(n)); }
        catch (e) { kids.textContent = e.message; loaded = false; }
      }
    };
    row.append(head, kids);
    return row;
  };
  api.get("/api/hist/tree").then(({ groups }) => {
    for (const g of groups) host.append(mk(g, async () => {
      const { subgroups } = await api.get(`/api/hist/tree?group=${encodeURIComponent(g)}`);
      return subgroups.map((s) => mk(s, async () => {
        const { descriptions } = await api.get(`/api/hist/tree?group=${encodeURIComponent(g)}&subgroup=${encodeURIComponent(s)}`);
        return descriptions.map((d) => mk(d, null, d));
      }));
    }));
  }).catch((e) => host.append(el("div", { class: "err" }, "historian: " + e.message)));
}

export class HistorianPage {
  constructor(root, app) {
    this.app = app;
    this.pens = lsGet(LS, []);   // [{desc, color, hidden}]
    root.classList.add("hist-page");
    const left = el("aside", { class: "hist-left" });
    const main = el("section", { class: "hist-main" });
    root.append(left, main);
    // ---- left: search + tree + selected pens
    this.search = el("input", { class: "tb-search", placeholder: "search historian (server side)…" });
    this.results = el("div", { class: "ht-results" });
    this.tree = el("div", { class: "ht-tree" });
    this.penList = el("div", { class: "pen-list" });
    left.append(el("div", { class: "pan-sub" }, "Historian tags"), this.search, this.results, this.tree, el("div", { class: "pan-sub row" }, "Selected pens", el("button", { class: "mini", onclick: () => { this.pens = []; this._pensChanged(); } }, "clear")), this.penList);
    this.search.oninput = debounce(() => this._search(), 350);
    // ---- toolbar
    const tb = el("div", { class: "toolbar hist-tb" });
    for (const [lbl, s] of RANGES) tb.append(el("button", { onclick: () => this.setSpan(s) }, lbl));
    this.from = el("input", { type: "datetime-local", step: 1 });
    this.to = el("input", { type: "datetime-local", step: 1 });
    this.liveBtn = el("button", { class: "on", title: "follow now", onclick: () => { this.trend.follow(!this.trend.o.live); this._liveUi(); this.load(); } }, "● Live");
    this.info = el("span", { class: "muted" });
    tb.append(el("span", { class: "sep" }), this.from, el("span", {}, "→"), this.to, el("button", { onclick: () => this._applyCustom() }, "Apply"),
      el("span", { class: "sep" }), this.liveBtn, el("button", { title: "previous period", onclick: () => this.shift(-1) }, "◀"), el("button", { title: "next period", onclick: () => this.shift(1) }, "▶"),
      el("span", { class: "sep" }), el("button", { onclick: () => this.exportCsv() }, "⇩ CSV"), el("button", { onclick: () => this.exportPng() }, "⇩ PNG"),
      el("span", { class: "spacer" }), this.info);
    this.chartHost = el("div", { class: "hist-chart" });
    this.stats = el("div", { class: "hist-stats" });
    main.append(tb, this.chartHost, this.stats, el("div", { class: "hint" }, "Wheel: zoom · drag: pan · Shift+drag: zoom to box · double-click: back to live · data is fetched lazily per day and downsampled per pixel on the server"));
    this.trend = new Trend(this.chartHost, { span: 3600 * 1000, live: true });
    this.trend.onRange = debounce(() => { this._liveUi(); this.load(); }, 250);
    this._pensChanged(false);
    this.timer = setInterval(() => { if (this.trend.o.live && this.root_visible()) { this.trend.tick(); if (++this.n % 15 === 0 && !this.loading) this.load(); } }, 1000);
    this.n = 0;
    this.rootEl = root;
    app.addEventListener("hist-pens", (e) => { for (const d of e.detail) this._toggle(d, true); });
  }

  root_visible() { return this.rootEl.style.display !== "none"; }

  onShow() {
    if (!this.treeLoaded) { this.treeLoaded = true; histTree(this.tree, (d) => this.pens.some((p) => p.desc === d), (d, c) => this._toggle(d, c)); }
    this.trend._resize();
    this.load();
  }

  _toggle(desc, on) {
    const has = this.pens.find((p) => p.desc === desc);
    if (on && !has) this.pens.push({ desc, color: PALETTE[this.pens.length % PALETTE.length] });
    if (!on && has) this.pens = this.pens.filter((p) => p !== has);
    this._pensChanged();
  }

  _pensChanged(reload = true) {
    lsSet(LS, this.pens);
    this.penList.innerHTML = "";
    this.pens.forEach((p, i) => {
      const co = el("input", { type: "color", value: p.color });
      co.onchange = () => { p.color = co.value; this._pensChanged(false); this.trend.pens[i].color = co.value; this.trend.draw(); };
      this.penList.append(el("div", { class: "pen" }, co, el("span", { title: p.desc }, p.desc), el("button", { class: "mini", onclick: () => this._toggle(p.desc, false) }, "×")));
    });
    this.trend.setPens(this.pens.map((p) => ({ name: p.desc, color: p.color })));
    this.tree.querySelectorAll(".ht-leaf").forEach((l) => { l.querySelector("input").checked = this.pens.some((p) => p.desc === l.title); });
    if (reload) this.load();
  }

  async _search() {
    const q = this.search.value.trim();
    this.results.innerHTML = "";
    if (!q) return;
    try {
      const r = await api.get(`/api/hist/search?q=${encodeURIComponent(q)}&limit=300`);
      for (const row of r.rows || []) {
        const cb = el("input", { type: "checkbox" });
        cb.checked = this.pens.some((p) => p.desc === row.description);
        cb.onchange = () => this._toggle(row.description, cb.checked);
        this.results.append(el("label", { class: "ht-leaf", title: `${row.group} / ${row.subgroup}` }, cb, el("span", {}, row.description), el("small", {}, ` ${row.subgroup}`)));
      }
      if (r.truncated) this.results.append(el("div", { class: "muted" }, "… more results, refine the search"));
    } catch (e) { this.results.append(el("div", { class: "err" }, e.message)); }
  }

  setSpan(s) { const t1 = Date.now(); this.trend.o.live = true; this.trend.setRange(t1 - s * 1000, t1); this._liveUi(); }
  shift(dir) { const s = this.trend.t1 - this.trend.t0; this.trend.setRange(this.trend.t0 + dir * s, this.trend.t1 + dir * s, true); }
  _applyCustom() { const a = new Date(this.from.value).getTime(), b = new Date(this.to.value).getTime(); if (a && b && b > a) this.trend.setRange(a, b, true); }
  _liveUi() {
    this.liveBtn.classList.toggle("on", this.trend.o.live);
    const f = (t) => { const d = new Date(t - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 19); };
    this.from.value = f(this.trend.t0); this.to.value = f(this.trend.t1);
  }

  async load() {
    if (!this.pens.length) { this.info.textContent = "select pens on the left"; this.stats.innerHTML = ""; return; }
    const my = (this.seq = (this.seq || 0) + 1);
    const t0 = Math.round(this.trend.t0), t1 = Math.round(this.trend.t1);
    const px = Math.max(200, Math.round(this.trend.W || 1200));
    this.loading = true;
    const t = performance.now();
    let done = 0, raw = 0, shown = 0;
    const rows = new Array(this.pens.length);
    const status = () => { this.info.textContent = `${done < this.pens.length ? `loading ${done}/${this.pens.length} pens… ` : ""}${raw.toLocaleString()} samples → ${shown.toLocaleString()} drawn · ${Math.round(performance.now() - t)} ms`; };
    status();
    // one request per pen: pens appear progressively, the server fetches them upstream in parallel
    await Promise.all(this.pens.map(async (p, i) => {
      try {
        const r = await api.get(`/api/hist/data?d=${encodeURIComponent(p.desc)}&t0=${t0}&t1=${t1}&px=${px}`);
        if (my !== this.seq) return;
        const s = r.series[p.desc] || { t: [], v: [], raw_count: 0 };
        this.trend.setData(i, s.t, s.v, s.type === 0);
        raw += s.raw_count; shown += s.t.length;
        const vals = s.v.filter((v) => v != null);
        let mn = null, mx = null, sum = 0;
        for (const v of vals) { if (mn === null || v < mn) mn = v; if (mx === null || v > mx) mx = v; sum += v; }
        rows[i] = `<tr><td><i style="background:${p.color}"></i>${esc(p.desc)}</td><td>${s.raw_count}</td><td>${fmt(mn)}</td><td>${fmt(mx)}</td><td>${fmt(vals.length ? sum / vals.length : null)}</td><td>${fmt(vals.length ? vals[vals.length - 1] : null)}</td></tr>`;
      } catch (e) { rows[i] = `<tr><td colspan="6" class="err">${esc(p.desc)}: ${esc(e.message)}</td></tr>`; }
      done++;
      if (my === this.seq) {
        status();
        this.stats.innerHTML = `<table><tr><th>Pen</th><th>Samples</th><th>Min</th><th>Max</th><th>Avg*</th><th>Last</th></tr>${rows.filter(Boolean).join("")}</table><small>* average of the downsampled points</small>`;
      }
    }));
    if (my === this.seq) this.loading = false;
  }

  exportCsv() {
    if (!this.pens.length) return;
    const q = this.pens.map((p) => "d=" + encodeURIComponent(p.desc)).join("&");
    location.href = `/api/hist/export.csv?${q}&t0=${Math.round(this.trend.t0)}&t1=${Math.round(this.trend.t1)}`;
  }
  exportPng() { const a = el("a", { href: this.trend.canvas.toDataURL("image/png"), download: "historian.png" }); a.click(); }
}

const fmt = (v) => (v == null ? "—" : (+v.toPrecision(6)).toString());

export async function pickHistorian() {
  const chosen = new Set();
  const host = el("div", { class: "ht-tree", style: { maxHeight: "60vh", overflow: "auto" } });
  const search = el("input", { class: "tb-search", placeholder: "search…" });
  const res = el("div", {});
  search.oninput = debounce(async () => {
    res.innerHTML = "";
    if (!search.value.trim()) return;
    const r = await api.get(`/api/hist/search?q=${encodeURIComponent(search.value)}&limit=200`);
    for (const row of r.rows || []) { const cb = el("input", { type: "checkbox" }); cb.onchange = () => (cb.checked ? chosen.add(row.description) : chosen.delete(row.description)); res.append(el("label", { class: "ht-leaf" }, cb, row.description)); }
  }, 300);
  histTree(host, (d) => chosen.has(d), (d, c) => (c ? chosen.add(d) : chosen.delete(d)));
  return modal({ title: "Select historian pens", width: 560, body: el("div", {}, search, res, host), buttons: [["Add", () => [...chosen], "primary"], ["Cancel", null]] });
}
