// Application shell: pages, runtime window manager, alarms, security, glue.
import { el, toast, modal, prompt, confirmBox, formatValue, esc } from "./util.js";
import { Connection, api, Q } from "./net.js";
import { Editor } from "./editor.js";
import { DisplayView, invalidateDoc } from "./view.js";
import { buildToolbox, buildExplorer, buildTagBrowser, buildInspector, buildLayers } from "./panels.js";
import { HistorianPage, pickHistorian } from "./historian.js";
import { TagsPage, AlarmsPage, StatusPage, ImportPage } from "./tables.js";

const ROLES = ["viewer", "operator", "engineer", "admin"];
const params = new URLSearchParams(location.search);

class App extends EventTarget {
  constructor() {
    super();
    this.conn = new Connection();
    this.alarms = []; this.alarmCounts = { active: 0, unack: 0 };
    this.aliasSet = localStorageGet("gf.aliasSet") || "Default";
    this.navStack = []; this.navPos = -1;
    this.popups = new Set();
    this.kiosk = params.get("kiosk") === "1";
    this.page = null;
    this.pages = {};
  }

  // ------------------------------------------------------------------ boot
  async start() {
    document.body.classList.toggle("kiosk", this.kiosk);
    this._shell();
    this.conn.addEventListener("welcome", (e) => { this.user = e.detail.user; this.role = e.detail.role; this._userBadge(); });
    this.conn.addEventListener("open", () => this._connBadge(true));
    this.conn.addEventListener("close", () => this._connBadge(false));
    this.conn.addEventListener("alarms", (e) => this._onAlarms(e.detail));
    this.conn.addEventListener("fs", (e) => this._onFs(e.detail));
    this.conn.addEventListener("aliases", (e) => { this.conn.aliases = e.detail.sets; this.refreshRuntime(); });
    this.conn.addEventListener("toast", (e) => toast(e.detail.msg, e.detail.level));
    this.conn.addEventListener("log", (e) => this._log(e.detail.msg));
    await this.refreshProject();
    const start = params.get("display") || this.project.cfg.startDisplay || this.project.displays[0]?.path;
    const mode = this.kiosk ? "run" : params.get("mode") || "edit";
    if (mode === "run") await this.run(start);
    else if (mode === "historian") this.show("historian");
    else if (start) await this.openInEditor(start);
    else this.show("edit");
    setInterval(() => this._rate(), 1000);
    window.addEventListener("beforeunload", (e) => { if (this.editor?.dirty) { e.preventDefault(); e.returnValue = ""; } });
    document.addEventListener("keydown", (e) => this._globalKey(e));
  }

  async refreshProject() {
    this.project = await api.get("/api/project");
    this.user = this.project.user; this.role = this.project.role;
    this.conn.aliases = this.project.aliases;
    this.explorer?.render();
    this._userBadge();
    this.$aliasSel && this._fillAliasSel();
    document.title = `${this.project.cfg.name || "Noelle"} — Noelle HMI`;
  }

  _shell() {
    const top = el("header", { id: "top" });
    this.$tabs = el("nav", { class: "tabs" });
    const tabs = [["edit", "✎ Editor"], ["run", "▶ Runtime"], ["historian", "🕓 Historian"], ["tags", "🏷 Tags & devices"], ["alarms", "🔔 Alarms"], ["status", "⚙ Diagnostics"], ["import", "⇪ Import"]];
    for (const [k, label] of tabs) this.$tabs.append(el("button", { "data-page": k, onclick: () => (k === "run" ? this.run(this.editor?.path || this.runPath) : this.show(k)) }, label));
    this.$title = el("span", { class: "doc-title" });
    this.$aliasSel = el("select", { class: "alias-sel", title: "global alias set", onchange: () => this.setAliasSet(this.$aliasSel.value) });
    this.$user = el("button", { class: "user", onclick: () => this._userMenu() });
    this.$conn = el("span", { class: "conn" });
    top.append(el("span", { class: "logo", title: "Noelle HMI" }, el("img", { src: "/web/img/noelle-logo.svg", alt: "" }), el("span", { class: "logo-name" }, "Noelle")), this.$tabs, this.$title, el("span", { class: "spacer" }), el("label", { class: "muted" }, "aliases "), this.$aliasSel, this.$user, this.$conn);
    this.$main = el("main", {});
    this.$status = el("footer", { id: "status" });
    this.$banner = el("div", { class: "alarm-banner", onclick: () => this.show("alarms") });
    this.$statusText = el("span", { class: "st" });
    this.$rate = el("span", { class: "rate" });
    this.muted = localStorageGet("noelle.muted") === "1";
    this.$mute = el("button", { class: "mute", onclick: () => this.toggleMute() });
    this._muteUi();
    this.$status.append(this.$statusText, el("span", { class: "spacer" }), this.$mute, this.$banner, this.$rate);
    document.body.append(top, this.$main, this.$status);
    this._fillAliasSel();
  }

  _fillAliasSel() {
    const sets = Object.keys(this.conn.aliases || { Default: {} });
    if (!sets.includes("Default")) sets.unshift("Default");
    this.$aliasSel.innerHTML = "";
    for (const s of sets) this.$aliasSel.append(el("option", { value: s, selected: s === this.aliasSet ? "selected" : null }, s));
  }

  show(page) {
    for (const b of this.$tabs.querySelectorAll("button")) b.classList.toggle("on", b.dataset.page === page);
    for (const [k, p] of Object.entries(this.pages)) p.root.style.display = k === page ? "" : "none";
    this.page = page;
    if (!this.pages[page]) this._createPage(page);
    this.pages[page].root.style.display = "";
    this.pages[page].onShow && this.pages[page].onShow();
    this.$title.textContent = page === "edit" ? (this.editor?.path || "") + (this.editor?.dirty ? " •" : "") : page === "run" ? this.runPath || "" : "";
  }

  _createPage(page) {
    const root = el("div", { class: "page page-" + page });
    this.$main.append(root);
    if (page === "edit") {
      const left = el("aside", { class: "left" }), center = el("section", { class: "center" }), right = el("aside", { class: "right" });
      root.append(left, center, right);
      const lt = el("div", { class: "ptabs" }), lbody = el("div", { class: "pbody" });
      left.append(lt, lbody);
      const panes = { Project: el("div"), Toolbox: el("div"), Tags: el("div"), Layers: el("div") };
      for (const [n, d] of Object.entries(panes)) { lt.append(el("button", { onclick: () => { Object.values(panes).forEach((p) => (p.style.display = "none")); d.style.display = ""; lt.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.textContent === n)); } }, n)); d.style.display = "none"; lbody.append(d); }
      panes.Project.style.display = ""; lt.firstChild.classList.add("on");
      const toolbar = el("div", { class: "toolbar" });
      const edHost = el("div", { class: "ed-wrap" });
      center.append(toolbar, edHost);
      this.editor = new Editor(this, edHost);
      buildToolbox(panes.Toolbox, this.editor);
      this.explorer = buildExplorer(panes.Project, this);
      buildTagBrowser(panes.Tags, this);
      buildLayers(panes.Layers, this.editor);
      const insp = el("div", { class: "inspector" });
      right.append(insp);
      buildInspector(insp, this.editor, this);
      this._toolbar(toolbar);
      this.editor.addEventListener("change", () => { this.$title.textContent = (this.editor.path || "") + (this.editor.dirty ? " •" : ""); this.explorer.render(); this._edStatus(); });
      this.editor.addEventListener("cursor", (e) => this._edStatus(e.detail));
      this.editor.addEventListener("selection", () => this._edStatus());
      this.pages.edit = { root, onShow: () => this.editor.drawOverlay() };
    } else if (page === "run") {
      const nav = el("div", { class: "run-nav" },
        el("button", { title: "back (Alt+←)", onclick: () => this.back() }, "◀"), el("button", { title: "forward (Alt+→)", onclick: () => this.forward() }, "▶"),
        el("button", { title: "home", onclick: () => this.run(this.project.cfg.startDisplay) }, "⌂"),
        el("select", { class: "run-displays", onchange: (e) => this.run(e.target.value) }),
        el("span", { class: "spacer" }),
        el("button", { title: "edit this display", onclick: () => this.openInEditor(this.runPath) }, "✎ Edit"),
        el("button", { title: "full screen (F11)", onclick: () => document.documentElement.requestFullscreen?.() }, "⛶"));
      const host = el("div", { class: "run-host" });
      root.append(nav, host);
      this.runHost = host;
      this.runView = new DisplayView({ conn: this.conn, host, mode: "run", app: this });
      new ResizeObserver(() => this._fitRuntime()).observe(host);
      this.pages.run = { root, onShow: () => { const s = nav.querySelector(".run-displays"); s.innerHTML = ""; for (const d of this.project.displays) s.append(el("option", { value: d.path, selected: d.path === this.runPath ? "selected" : null }, d.name)); } };
    } else if (page === "historian") { const p = new HistorianPage(root, this); this.pages.historian = { root, onShow: () => p.onShow() }; }
    else if (page === "tags") { const p = new TagsPage(root, this); this.pages.tags = { root, onShow: () => p.load() }; }
    else if (page === "alarms") { const p = new AlarmsPage(root, this); this.pages.alarms = { root, onShow: () => p.render() }; }
    else if (page === "status") { const p = new StatusPage(root, this); this.pages.status = { root, onShow: () => p.start() }; }
    else if (page === "import") { const p = new ImportPage(root, this); this.pages.import = { root, onShow: () => p.render() }; }
  }

  _toolbar(tb) {
    const ed = this.editor;
    const B = (label, title, fn, cls = "") => el("button", { title, class: cls, onclick: fn }, label);
    const sep = () => el("span", { class: "sep" });
    const snap = B("⊞ Snap", "snap to grid (G)", () => { ed.snap = !ed.snap; snap.classList.toggle("on", ed.snap); }, "on");
    const grid = B("# Grid", "show grid", () => { ed.showGrid = !ed.showGrid; grid.classList.toggle("on", ed.showGrid); ed.drawOverlay(); }, "on");
    const guides = B("⌖ Guides", "smart guides", () => { ed.guides = !ed.guides; guides.classList.toggle("on", ed.guides); }, "on");
    const zoomLbl = el("span", { class: "zoom" }, "100%");
    ed.addEventListener("zoom", () => (zoomLbl.textContent = Math.round(ed.zoom * 100) + "%"));
    tb.append(
      B("💾", "save (Ctrl+S)", () => ed.save()), B("↶", "undo (Ctrl+Z)", () => ed.undo()), B("↷", "redo (Ctrl+Y)", () => ed.redo()), sep(),
      B("▶ Test", "test run this display without saving (F5)", () => this.testRun()), sep(),
      B("⇤", "align left", () => ed.align("left")), B("⇔", "align center", () => ed.align("hcenter")), B("⇥", "align right", () => ed.align("right")),
      B("⤒", "align top", () => ed.align("top")), B("⇕", "align middle", () => ed.align("vcenter")), B("⤓", "align bottom", () => ed.align("bottom")),
      B("⋯", "distribute horizontally", () => ed.distribute("h")), B("⋮", "distribute vertically", () => ed.distribute("v")),
      B("▭=", "same size", () => ed.sameSize("both")), B("⇋", "flip horizontal", () => ed.flip("h")), sep(),
      B("⬆", "bring to front", () => ed.zorder("front")), B("⬇", "send to back", () => ed.zorder("back")), B("⧉", "group (Ctrl+G)", () => ed.group()), B("⧈", "ungroup", () => ed.ungroup()), B("🔒", "lock (Ctrl+L)", () => ed.toggleLock()), sep(),
      B("◈ Symbol", "make symbol from selection", () => ed.makeSymbol()), B("⇄ Replace", "find & replace tags/text", () => ed.findReplace()), sep(),
      snap, grid, guides, B("−", "zoom out", () => ed.setZoom(ed.zoom / 1.25)), zoomLbl, B("+", "zoom in", () => ed.setZoom(ed.zoom * 1.25)), B("Fit", "fit (Ctrl+0)", () => ed.fit()), sep(),
      B("{ } VS Code", "open this display file in VS Code", () => this.vscode(ed.path)));
  }

  _edStatus(c) {
    const ed = this.editor;
    const parts = [];
    if (c && c.x !== undefined) parts.push(`x ${Math.round(c.x)}  y ${Math.round(c.y)}` + (c.w !== undefined ? `  w ${Math.round(c.w)}  h ${Math.round(c.h)}` : ""));
    if (c && c.rot !== undefined) parts.push(`rotation ${c.rot}°`);
    parts.push(`${ed.sel.size} selected`, `${ed.objects?.length || 0} objects`, ed.snap ? `snap ${ed.grid}` : "no snap", ed.tool ? `tool: ${ed.tool}` : "");
    this.$statusText.textContent = parts.filter(Boolean).join("   ·   ");
  }

  _globalKey(e) {
    if (e.key === "F5") { e.preventDefault(); if (this.page === "edit") this.testRun(); else if (this.page === "run" && this.runPath) this.run(this.runPath); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && this.page === "edit") { e.preventDefault(); this.editor.save(); }
    if (e.altKey && e.key === "ArrowLeft") this.back();
    if (e.altKey && e.key === "ArrowRight") this.forward();
  }

  // ------------------------------------------------------------------ editor
  async openInEditor(path) {
    if (!path) return;
    if (this.editor?.dirty && this.editor.path !== path && !(await confirmBox(`Discard unsaved changes in ${this.editor.path}?`))) return;
    this.show("edit");
    invalidateDoc(path);
    const text = await api.text(`/api/file?path=${encodeURIComponent(path)}`);
    let doc;
    try { doc = JSON.parse(text); } catch (e) { toast(`${path}: invalid JSON (${e.message}) — fix it in VS Code`, "error", 8000); this.vscode(path); return; }
    await this.editor.open(path, doc);
    this.show("edit");
  }

  async newDisplay(folder) {
    const name = await prompt(`New ${folder === "symbols" ? "symbol" : "display"} name`, folder === "symbols" ? "NewSymbol" : "NewDisplay");
    if (!name) return;
    const path = `${folder}/${name}.nll`;
    const doc = { format: "noelle-display", version: 1, name, size: folder === "symbols" ? [100, 100] : [1280, 720], background: folder === "symbols" ? "transparent" : "#2b2f36", aliases: {}, objects: [] };
    await api.put(`/api/file?path=${encodeURIComponent(path)}`, JSON.stringify(doc, null, 2) + "\n", true);
    await this.refreshProject();
    await this.openInEditor(path);
  }

  async testRun() {
    const doc = JSON.parse(this.editor.snapshot());
    const back = el("div", { class: "modal-back test-run" });
    const box = el("div", { class: "test-box" });
    const host = el("div", { class: "test-host" });
    const close = () => { v.destroy(); back.remove(); document.removeEventListener("keydown", key, true); };
    const key = (e) => { if (e.key === "Escape" || e.key === "F5") { e.preventDefault(); e.stopPropagation(); close(); } };
    box.append(el("div", { class: "test-head" }, `Test run: ${this.editor.path} (live data, unsaved changes) — Esc to close`, el("button", { onclick: close }, "✕")), host);
    back.append(box); document.body.append(back);
    document.addEventListener("keydown", key, true);
    const v = new DisplayView({ conn: this.conn, host, mode: "run", app: this });
    await v.setDoc(doc);
    fitSvg(v.svg, host);
  }

  // ------------------------------------------------------------------ runtime
  async run(path, aliases = {}, pushNav = true) {
    path = path || this.project.cfg.startDisplay || this.project.displays[0]?.path;
    if (!path) { toast("no display"); return; }
    this.show("run");
    this.runPath = path;
    this.runView.instanceAliases = aliases;
    try { await this.runView.load(path); } catch (e) { toast(`${path}: ${e.message}`, "error"); return; }
    this._fitRuntime();
    if (pushNav) { this.navStack = this.navStack.slice(0, this.navPos + 1); this.navStack.push([path, aliases]); this.navPos = this.navStack.length - 1; }
    this.$title.textContent = path;
    this.pages.run.onShow();
  }

  _fitRuntime() { if (this.runView?.svg) fitSvg(this.runView.svg, this.runHost); }
  back() { if (this.navPos > 0) { this.navPos--; const [p, a] = this.navStack[this.navPos]; this.run(p, a, false); } }
  forward() { if (this.navPos < this.navStack.length - 1) { this.navPos++; const [p, a] = this.navStack[this.navPos]; this.run(p, a, false); } }
  refreshRuntime() { if (this.page === "run" && this.runPath) this.run(this.runPath, this.runView.instanceAliases, false); this.popups.forEach((p) => p.reload()); }

  async openDisplay(path, { target = "main", aliases = {}, title = "", x, y, modal: isModal } = {}) {
    if (target === "main") return this.run(path, aliases);
    if (target === "window") {
      const u = new URL(location.href);
      u.search = `?mode=run&kiosk=1&display=${encodeURIComponent(path)}`;
      window.open(u, "_blank", "popup,width=1280,height=760");
      return;
    }
    // popup / faceplate (draggable, several can be open, same display+aliases is reused)
    const key = path + JSON.stringify(aliases);
    for (const p of this.popups) if (p.key === key) { p.front(); return p; }
    const pop = new Popup(this, path, aliases, title, x, y, isModal);
    pop.key = key;
    this.popups.add(pop);
    await pop.open();
    return pop;
  }

  setAliasSet(name) {
    this.aliasSet = name;
    try { localStorage.setItem("gf.aliasSet", name); } catch { /* ignore */ }
    if (this.$aliasSel) this.$aliasSel.value = name;
    this.refreshRuntime();
    toast(`alias set: ${name}`);
  }

  // ------------------------------------------------------------------ writes / entry
  hasRole(role) { return ROLES.indexOf(this.role) >= ROLES.indexOf(role); }
  canWrite() { if (this.hasRole("operator")) return true; toast("operator role required — log in", "warn"); return false; }

  async write(ref, value) {
    if (!this.canWrite()) return;
    try { await this.conn.write(ref, value); } catch (e) { toast(`write ${ref}: ${e.message}`, "error"); }
  }

  confirm(text) { return confirmBox(text); }

  async dataEntry({ tag, min, max, fmt, confirm, title }) {
    if (!tag || !this.canWrite()) return;
    const cur = this.conn.value(tag);
    const inp = el("input", { class: "keypad-input", value: cur === undefined ? "" : formatValue(cur, fmt).replace(/\s/g, "") });
    const pad = el("div", { class: "keypad" });
    for (const k of ["7", "8", "9", "←", "4", "5", "6", "C", "1", "2", "3", "±", "0", ".", "-", "E"]) {
      pad.append(el("button", { onclick: () => {
        if (k === "←") inp.value = inp.value.slice(0, -1); else if (k === "C") inp.value = ""; else if (k === "±") inp.value = inp.value.startsWith("-") ? inp.value.slice(1) : "-" + inp.value; else if (k === "E") inp.value += "e"; else inp.value += k;
        inp.focus();
      } }, k));
    }
    const lim = (min !== "" && min != null) || (max !== "" && max != null) ? `range ${min ?? "−∞"} … ${max ?? "+∞"}` : "";
    const res = await modal({ title: title || `Set ${tag}`, width: 300, body: el("div", {}, el("div", { class: "muted" }, `${tag}   current: ${cur === undefined ? "?" : formatValue(cur, fmt)}   ${lim}`), inp, pad), buttons: [["Write", () => inp.value, "primary"], ["Cancel", null]] });
    if (res === null || res === undefined || res === "") return;
    let v = res.replace(",", ".");
    const n = Number(v);
    if (!isNaN(n)) {
      if (min !== "" && min != null && n < +min) { toast(`value below minimum ${min}`, "error"); return; }
      if (max !== "" && max != null && n > +max) { toast(`value above maximum ${max}`, "error"); return; }
      v = n;
    }
    if (confirm && !(await confirmBox(`Write ${v} to ${tag}?`))) return;
    await this.write(tag, v);
  }

  // ------------------------------------------------------------------ alarms
  toggleMute() {
    this.muted = !this.muted;
    try { localStorage.setItem("noelle.muted", this.muted ? "1" : "0"); } catch { /* ignore */ }
    this._muteUi();
    toast(this.muted ? "alarm sound muted" : "alarm sound on");
  }
  _muteUi() {
    this.$mute.textContent = this.muted ? "🔕 muted" : "🔔";
    this.$mute.title = this.muted ? "alarm sound is muted - click to unmute" : "click to mute the alarm sound";
    this.$mute.classList.toggle("on", this.muted);
  }

  _onAlarms(m) {
    this.alarms = m.list; this.alarmCounts = { active: m.active, unack: m.unack };
    const top = m.list.find((a) => !a.acked) || m.list[0];
    this.$banner.innerHTML = "";
    if (top) {
      this.$banner.className = "alarm-banner " + (m.unack ? "unack" : "active");
      this.$banner.append(el("b", {}, `${m.active} active / ${m.unack} unacked`), el("span", {}, `  ${new Date(top.t).toLocaleTimeString()}  ${top.msg}`),
        el("button", { onclick: (e) => { e.stopPropagation(); this.ackAlarms(null); } }, "ACK ALL"));
      if (m.unack && !this.muted && this.project?.cfg.alarms?.sound !== false) beep();
    } else this.$banner.className = "alarm-banner";
    this.dispatchEvent(new Event("alarms"));
  }
  async ackAlarms(ids) { if (!this.canWrite()) return; try { await this.conn.rpc("ack", { ids }); } catch (e) { toast(e.message, "error"); } }

  async recipe(name, mode) {
    if (mode === "save") { const r = await api.post(`/api/recipes/${encodeURIComponent(name)}/snapshot`, {}); toast(`recipe ${name} saved (${r.length} values)`); return; }
    if (!(await confirmBox(`Download recipe '${name}' to the process?`))) return;
    const res = await api.post(`/api/recipes/${encodeURIComponent(name)}/apply`, {});
    const bad = res.filter((r) => !r.ok);
    toast(bad.length ? `recipe ${name}: ${bad.length} write(s) failed` : `recipe ${name} applied (${res.length} values)`, bad.length ? "error" : "info");
  }

  // ------------------------------------------------------------------ security
  async login() {
    const u = el("input", { placeholder: "user" }), p = el("input", { type: "password", placeholder: "password" });
    const r = await modal({ title: "Log in", body: el("div", { class: "form" }, u, p), buttons: [["Log in", () => [u.value, p.value], "primary"], ["Cancel", null]] });
    if (!r) return;
    try { await api.post("/api/login", { user: r[0], password: r[1] }); location.reload(); } catch (e) { toast(e.message, "error"); }
  }
  async logout() { await api.post("/api/logout", {}); location.reload(); }
  _userMenu() {
    const r = this.$user.getBoundingClientRect();
    this.contextMenu(r.left, r.bottom, [["Log in…", "", () => this.login()], ["Log out", "", () => this.logout()], null, [`${this.user} (${this.role})`, "", () => {}]]);
  }
  _userBadge() { if (this.$user) this.$user.textContent = `👤 ${this.user || "?"} · ${this.role || ""}`; }
  _connBadge(ok) { this.$conn.textContent = ok ? "● online" : "● offline"; this.$conn.className = "conn " + (ok ? "ok" : "bad"); }

  // ------------------------------------------------------------------ misc services
  async vscode(path) { try { await api.post("/api/vscode", { path }); toast(`opening ${path || "project"} in VS Code`); } catch (e) { toast(e.message, "error"); } }
  pickHistorian() { return pickHistorian(this); }
  openHistorian(pens) { this.show("historian"); if (pens?.length) this.pages.historian && this.pages.historian.root && this._histPens(pens); }
  _histPens(pens) { this.dispatchEvent(new CustomEvent("hist-pens", { detail: pens })); }

  async history(path) {
    const list = await api.get(`/api/file/history?path=${encodeURIComponent(path)}`);
    if (!list.length) { toast("no saved versions yet"); return; }
    const sel = el("select", { size: 12, style: { width: "100%" } }, list.map((v) => el("option", { value: v }, v.replace(/(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)/, "$1-$2-$3 $4:$5:$6"))));
    const v = await modal({ title: `Versions of ${path}`, body: sel, buttons: [["Restore", () => sel.value, "primary"], ["Cancel", null]] });
    if (!v) return;
    const text = await api.text(`/api/file/version?path=${encodeURIComponent(path)}&v=${encodeURIComponent(v)}`);
    await api.put(`/api/file?path=${encodeURIComponent(path)}`, text, true);
    toast(`restored ${path} from ${v}`);
    if (this.editor?.path === path) { this.editor.dirty = false; this.openInEditor(path); }
  }

  contextMenu(x, y, items) {
    document.querySelectorAll(".ctx").forEach((m) => m.remove());
    const m = el("div", { class: "ctx", style: { left: x + "px", top: y + "px" } });
    for (const it of items) {
      if (!it) { m.append(el("hr")); continue; }
      m.append(el("div", { class: "ctx-item", onclick: () => { m.remove(); it[2](); } }, el("span", {}, it[0]), el("small", {}, it[1] || "")));
    }
    document.body.append(m);
    const r = m.getBoundingClientRect();
    if (r.bottom > innerHeight) m.style.top = Math.max(0, innerHeight - r.height - 4) + "px";
    if (r.right > innerWidth) m.style.left = Math.max(0, innerWidth - r.width - 4) + "px";
    setTimeout(() => document.addEventListener("pointerdown", (e) => { if (!m.contains(e.target)) m.remove(); }, { once: true }));
  }

  async _onFs(m) {
    if (m.path?.endsWith(".nll")) invalidateDoc(m.path);
    if (m.path && !m.path.endsWith("/")) {
      await this.refreshProject();
    }
    // external edit (VS Code / Notepad) of the open display -> reload
    if (m.external && this.editor && m.path === this.editor.path) {
      if (!this.editor.dirty) { await this.openInEditor(m.path); toast(`${m.path} changed on disk — reloaded`); }
      else toast(`${m.path} changed on disk (you have unsaved changes)`, "warn", 6000);
    }
    if (this.page === "run" && (m.path === this.runPath || m.path?.startsWith("symbols/") || m.path?.startsWith("scripts/"))) this.refreshRuntime();
  }

  _log(msg) { console.log("[server]", msg); if (/ERROR/.test(msg)) toast(msg.split("\n")[0], "error", 6000); }

  _rate() {
    const s = this.conn.stats;
    const v = s.values - (this._lastVals || 0);
    this._lastVals = s.values;
    this.$rate.textContent = `${this.conn.points.size} tags · ${v} upd/s`;
  }
}

class Popup {
  constructor(app, path, aliases, title, x, y, isModal) {
    Object.assign(this, { app, path, aliases, title, x, y, isModal });
    this.win = el("div", { class: "popup" });
    this.head = el("div", { class: "popup-head" }, el("span", { class: "t" }, title || path), el("button", { onclick: () => this.close() }, "✕"));
    this.body = el("div", { class: "popup-body" });
    this.win.append(this.head, this.body);
    if (isModal) { this.back = el("div", { class: "modal-back" }); this.back.append(this.win); document.body.append(this.back); }
    else document.body.append(this.win);
    this.view = new DisplayView({ conn: app.conn, host: this.body, mode: "run", app, aliases, popup: this });
    this._drag();
    this.win.addEventListener("pointerdown", () => this.front());
  }
  async open() {
    const doc = await this.view.load(this.path);
    const [W, H] = doc.size || [400, 300];
    this.win.style.width = W + "px";
    const r = { x: this.x ?? (innerWidth - W) / 2 + (this.app.popups.size - 1) * 24, y: this.y ?? Math.max(40, (innerHeight - H) / 2 + (this.app.popups.size - 1) * 24) };
    this.win.style.left = Math.max(0, r.x) + "px"; this.win.style.top = Math.max(0, r.y) + "px";
    if (!this.title) this.head.querySelector(".t").textContent = doc.name || this.path;
    this.front();
  }
  reload() { this.view.load(this.path); }
  front() { Popup.z = (Popup.z || 100) + 1; this.win.style.zIndex = Popup.z; }
  close() { this.view.destroy(); (this.back || this.win).remove(); this.app.popups.delete(this); }
  _drag() {
    this.head.addEventListener("pointerdown", (e) => {
      if (e.target.tagName === "BUTTON") return;
      const x0 = e.clientX - this.win.offsetLeft, y0 = e.clientY - this.win.offsetTop;
      this.head.setPointerCapture(e.pointerId);
      const mv = (ev) => { this.win.style.left = ev.clientX - x0 + "px"; this.win.style.top = Math.max(0, ev.clientY - y0) + "px"; };
      const up = () => { this.head.removeEventListener("pointermove", mv); this.head.removeEventListener("pointerup", up); };
      this.head.addEventListener("pointermove", mv); this.head.addEventListener("pointerup", up);
    });
  }
}

export function fitSvg(svgEl, host) {
  if (!svgEl) return;
  const vb = svgEl.viewBox.baseVal;
  const s = Math.min(host.clientWidth / vb.width, host.clientHeight / vb.height);
  svgEl.setAttribute("width", Math.floor(vb.width * s));
  svgEl.setAttribute("height", Math.floor(vb.height * s));
}

let audioCtx, lastBeep = 0;
function beep() {
  if (Date.now() - lastBeep < 4000 || !navigator.userActivation?.hasBeenActive) return;
  lastBeep = Date.now();
  try {
    audioCtx = audioCtx || new AudioContext();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; g.gain.value = 0.05;
    o.connect(g); g.connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime + 0.18);
  } catch { /* audio blocked */ }
}

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }

// ?render=1&display=<path>: static 1:1 rendering (no UI) used by the visual identity tests
async function renderOnly() {
  document.body.classList.add("render");
  const host = el("div", { class: "render-host" });
  document.body.append(host);
  const conn = { aliases: {}, points: new Map(), subscribe() {}, unsubscribe() {}, value() {} };
  try { const p = await api.get("/api/project"); conn.aliases = p.aliases; } catch { /* ignore */ }
  const view = new DisplayView({ conn, host, mode: "edit", app: { aliasSet: "Default" } });
  const doc = JSON.parse(await api.text(`/api/file?path=${encodeURIComponent(params.get("display"))}`));
  await view.setDoc(doc);
  await new Promise((r) => setTimeout(r, 300));    // symbols load asynchronously
  const imgs = [...view.svg.querySelectorAll("image")].map((i) => new Promise((res) => { const im = new Image(); im.onload = im.onerror = res; im.src = i.getAttribute("href"); }));
  await Promise.all(imgs);
  await document.fonts.ready;
  window.__renderSize = doc.size || [1280, 720];
  window.__rendered = true;
}

if (params.get("render") === "1") renderOnly();
else {
  const app = new App();
  window.gf = app;
  app.start().catch((e) => { console.error(e); document.body.append(el("pre", { class: "fatal" }, String(e.stack || e))); });
}
export { esc, Q };
