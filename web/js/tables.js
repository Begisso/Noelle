// Tags & devices, alarms, diagnostics, import pages.
import { el, toast, confirmBox, download, formatValue, esc, debounce } from "./util.js";
import { api, Q } from "./net.js";
import { pickPath, buildAddress } from "./dialogs.js";

// =====================================================================================
// Tags (tags.csv), aliases (aliases.csv), devices (project.nll)
// =====================================================================================
export class TagsPage {
  constructor(root, app) {
    this.app = app;
    this.root = root;
    root.classList.add("tables-page");
    const tabs = el("div", { class: "ptabs" });
    this.body = el("div", { class: "tbl-body" });
    root.append(tabs, this.body);
    this.tab = "tags";
    for (const [k, l] of [["tags", "Tag database (tags.csv)"], ["aliases", "Global aliases (aliases.csv)"], ["devices", "Devices (project.nll)"]]) {
      tabs.append(el("button", { class: k === this.tab ? "on" : "", onclick: (e) => { this.tab = k; tabs.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); this.load(); } }, l));
    }
  }

  async load() {
    this.body.innerHTML = "";
    if (this.tab === "devices") return this._devices();
    const isTags = this.tab === "tags";
    const data = await api.get(isTags ? "/api/tags" : "/api/aliases");
    const cols = data.columns;
    let rows = data.rows;
    for (const r of rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
    const filter = el("input", { class: "tb-search", placeholder: "filter…" });
    const file = el("input", { type: "file", accept: ".csv,.txt", style: { display: "none" } });
    const tb = el("div", { class: "toolbar" },
      el("button", { onclick: () => save() }, "💾 Save"), el("button", { onclick: () => { rows.push({}); draw(); } }, "+ Row"),
      isTags ? el("button", { title: "choose Siemens / Modbus / OPC UA…, type the IP and address, test it live", onclick: async () => {
        const row = await buildAddress(this.app, { tagMode: true });
        if (row) { rows.push(row); draw(); toast(`tag ${row.Name} added — click Save`); }
      } }, "+ Tag (builder)…") : "",
      el("button", { onclick: () => file.click() }, "⇧ Import CSV (merge)"),
      el("button", { onclick: () => download(isTags ? "tags.csv" : "aliases.csv", toCsv(cols, rows), "text/csv") }, "⇩ Export CSV"),
      el("button", { onclick: () => this.app.vscode(isTags ? "tags.csv" : "aliases.csv") }, "{ } VS Code"), filter, file,
      el("span", { class: "muted" }, isTags ? "Address: direct address, '=expression' (Python syntax, {{ref}}) or empty = memory tag. Alarms: HiHi/Hi/Lo/LoLo limits or Digital value. Log: 'change' or ms." : "Set,Alias,Value — switch sets at runtime (top bar) like ICONICS global alias themes."));
    const tableHost = el("div", { class: "grid-host" });
    this.body.append(tb, tableHost);
    const draw = () => {
      const f = filter.value.toLowerCase();
      const t = el("table", { class: "grid" });
      t.append(el("tr", {}, el("th", {}, ""), cols.map((c) => el("th", {}, c)), isTags ? el("th", {}, "Live") : ""));
      const live = [];
      rows.forEach((r, i) => {
        if (f && !Object.values(r).join(" ").toLowerCase().includes(f)) return;
        const tr = el("tr", {}, el("td", {}, el("button", { class: "mini", onclick: () => { rows.splice(i, 1); draw(); } }, "×")));
        for (const c of cols) {
          const inp = el("input", { value: r[c] ?? "" });
          inp.onchange = () => { r[c] = inp.value; };
          tr.append(el("td", {}, inp));
        }
        if (isTags && r.Name) { const td = el("td", { class: "live" }); tr.append(td); live.push([r.Name, td]); }
        t.append(tr);
      });
      tableHost.innerHTML = ""; tableHost.append(t);
      // live values for visible rows (subscribed lazily, unsubscribed on redraw)
      (this.liveSubs || []).forEach(([ref, fn]) => this.app.conn.unsubscribe(ref, fn));
      this.liveSubs = live.slice(0, 300).map(([name, td]) => { const fn = (p) => { td.textContent = p.q >= Q.GOOD ? formatValue(p.value) : `bad (${p.q})`; td.className = "live " + (p.q >= Q.GOOD ? "ok" : "bad"); td.title = p.meta?.err || ""; }; this.app.conn.subscribe(name, fn); return [name, fn]; });
    };
    filter.oninput = debounce(draw, 200);
    const save = async () => {
      try { await api.put(isTags ? "/api/tags" : "/api/aliases", { rows: rows.filter((r) => Object.values(r).some((v) => v)) }); toast("saved"); this.app.refreshProject(); }
      catch (e) { toast(e.message, "error"); }
    };
    file.onchange = async () => {
      const text = await file.files[0].text();
      if (isTags) {
        const r = await api.post("/api/tags/import?mode=merge", text, true);
        toast(`imported ${r.imported} tags (total ${r.total})`); this.load();
      } else {
        const add = parseCsv(text);
        rows = rows.concat(add); draw(); toast(`${add.length} rows added — click Save`);
      }
      file.value = "";
    };
    draw();
  }

  async _devices() {
    const p = await api.get("/api/project");
    const ta = el("textarea", { class: "code", spellcheck: "false" });
    ta.value = JSON.stringify(p.cfg.devices || [], null, 2);
    const help = el("pre", { class: "help" }, DEVICE_HELP);
    this.body.append(el("div", { class: "toolbar" },
      el("button", { onclick: async () => {
        let devs;
        try { devs = JSON.parse(ta.value); } catch (e) { toast("invalid JSON: " + e.message, "error"); return; }
        await api.put("/api/project/config", { ...p.cfg, devices: devs });
        toast("devices saved"); this.app.refreshProject();
      } }, "💾 Save devices"), el("button", { onclick: () => this.app.vscode("project.nll") }, "{ } Edit project.nll in VS Code")),
      el("div", { class: "split" }, ta, help));
  }
}

const DEVICE_HELP = `Device examples (project.nll → "devices"):

{"name":"PLC1","type":"modbus","host":"192.168.1.10","port":502,"unit":1,
 "poll_ms":500,"word_swap":false,"max_gap":16,"max_inflight":1}
{"name":"RTU1","type":"modbus","serial":"COM3","baud":9600,"parity":"N","unit":5}
{"name":"GW1","type":"modbus","host":"10.0.0.9","port":4001,"framing":"rtu","unit":2}

{"name":"S7_1500","type":"s7","host":"192.168.0.1","rack":0,"slot":1,"poll_ms":250}
   S7-300/400: slot 2.  S7-1200/1500: enable PUT/GET, DBs without "optimized access"

{"name":"UA","type":"opcua","endpoint":"opc.tcp://192.168.0.1:4840",
 "username":"","password":"","publish_ms":250}

{"name":"DataHub","type":"opcda","server":"Cogent.DataHub.1","rate_ms":250}
{"name":"Kepware","type":"opcda","server":"Kepware.KEPServerEX.V6"}
{"name":"DH_TCP","type":"datahub","host":"127.0.0.1","port":4502,"domain":"default"}
   (DataHub TCP API needs a DataHub license with API/TCP clients)

Use in displays:  {{PLC1/40001:F}} is written as {{modbus:PLC1/40001:F}}
                  {{s7:S7_1500/DB10.DBD4}}  {{opcda:DataHub/default:Tank1}}
Direct (no device needed):  {{modbus:[192.168.1.10:502,1]40001}}`;

function toCsv(cols, rows) {
  const q = (v) => { v = v == null ? "" : String(v); return /[",;\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
  return "\ufeff" + [cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\r\n");
}
function parseCsv(text) {
  const lines = text.replace(/^\ufeff/, "").split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return [];
  const d = (lines[0].match(/;/g) || []).length > (lines[0].match(/,/g) || []).length ? ";" : ",";
  const split = (l) => { const out = []; let cur = "", q = false; for (let i = 0; i < l.length; i++) { const c = l[i]; if (q) { if (c === '"' && l[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; } else if (c === '"') q = true; else if (c === d) { out.push(cur); cur = ""; } else cur += c; } out.push(cur); return out; };
  const head = split(lines[0]).map((h) => h.trim());
  return lines.slice(1).map((l) => { const v = split(l); return Object.fromEntries(head.map((h, i) => [h, (v[i] ?? "").trim()])); });
}

// =====================================================================================
// Alarms
// =====================================================================================
export class AlarmsPage {
  constructor(root, app) {
    this.app = app;
    root.classList.add("tables-page");
    this.day = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
    this.cur = el("div", { class: "grid-host" });
    this.hist = el("div", { class: "grid-host" });
    root.append(el("div", { class: "toolbar" }, el("button", { onclick: () => app.ackAlarms(null) }, "✔ Acknowledge all"),
      el("button", { title: "mute / unmute the alarm sound", onclick: () => app.toggleMute() }, "🔕 Mute sound"), el("span", { class: "sep" }), el("span", {}, "History "), this.day, el("button", { onclick: () => this.history() }, "Load"), el("button", { onclick: () => this.exportHist() }, "⇩ CSV")),
      el("div", { class: "pan-sub" }, "Current alarms"), this.cur, el("div", { class: "pan-sub" }, "Alarm history"), this.hist);
    app.addEventListener("alarms", () => this.render());
  }
  render() {
    const t = el("table", { class: "grid alarms" }, el("tr", {}, ["Time", "Prio", "Tag", "Condition", "Limit", "Value", "Message", "State", ""].map((h) => el("th", {}, h))));
    for (const a of this.app.alarms) {
      t.append(el("tr", { class: `al ${a.active ? "act" : "rtn"} ${a.acked ? "ack" : "unack"}` }, el("td", {}, new Date(a.t).toLocaleString()), el("td", {}, a.prio), el("td", {}, a.tag), el("td", {}, a.cond), el("td", {}, a.limit ?? ""),
        el("td", {}, a.value == null ? "" : formatValue(a.value)), el("td", {}, a.msg), el("td", {}, (a.active ? "ACTIVE" : "RTN") + (a.acked ? " · ACK " + (a.user || "") : "")),
        el("td", {}, a.acked ? "" : el("button", { class: "mini", onclick: () => this.app.ackAlarms([a.id]) }, "ACK"))));
    }
    this.cur.innerHTML = ""; this.cur.append(this.app.alarms.length ? t : el("div", { class: "muted" }, "no active or unacknowledged alarms"));
    if (!this.loaded) { this.loaded = true; this.history(); }
  }
  async history() {
    this.rows = await api.get(`/api/alarms/history?day=${this.day.value}`);
    const cols = this.rows.length ? Object.keys(this.rows[0]) : [];
    const t = el("table", { class: "grid" }, el("tr", {}, cols.map((c) => el("th", {}, c))));
    for (const r of [...this.rows].reverse()) t.append(el("tr", { class: "ev-" + (r.Event || "").toLowerCase() }, cols.map((c) => el("td", {}, r[c]))));
    this.hist.innerHTML = ""; this.hist.append(this.rows.length ? t : el("div", { class: "muted" }, "no events"));
  }
  exportHist() { if (this.rows?.length) download(`alarms-${this.day.value}.csv`, toCsv(Object.keys(this.rows[0]), this.rows), "text/csv"); }
}

// =====================================================================================
// Diagnostics
// =====================================================================================
export class StatusPage {
  constructor(root, app) { this.app = app; this.root = root; root.classList.add("tables-page"); this.box = el("div", {}); root.append(this.box); }
  start() { this.tick(); clearInterval(this.t); this.t = setInterval(() => { if (this.root.style.display === "none") { clearInterval(this.t); return; } this.tick(); }, 2000); }
  async tick() {
    const s = await api.get("/api/status");
    const drv = Object.entries(s.drivers).map(([k, d]) => `<tr class="${d.connected ? "ok" : "bad"}"><td>${esc(k)}</td><td>${esc(d.kind || "")}</td><td>${d.connected ? "●" : "○"}</td><td>${d.tags ?? ""}</td><td>${d.reads ?? ""}</td><td>${d.writes ?? ""}</td><td>${d.errors ?? ""}</td><td>${d.rtt_ms ?? ""}</td><td>${d.blocks ?? d.requests_per_scan ?? ""}</td><td>${esc(d.error || "")}</td></tr>`).join("");
    const h = s.historian ? `<tr><td>${esc(s.historian.url)}</td><td>${s.historian.cached_chunks}</td><td>${s.historian.upstream}</td><td>${s.historian.cache_hits}</td><td>${s.historian.errors}</td><td>${esc(s.historian.last_error || "")}</td></tr>` : "";
    const scr = s.scripts;
    this.box.innerHTML = `
      <div class="pan-sub">Server</div>
      <table class="grid"><tr><th>Tags</th><th>Active (polled)</th><th>Clients</th><th>Updates</th><th>Writes</th><th>Browser: subscribed</th><th>msgs</th></tr>
      <tr><td>${s.tags}</td><td>${s.active}</td><td>${s.clients}</td><td>${s.updates}</td><td>${s.writes}</td><td>${this.app.conn.points.size}</td><td>${this.app.conn.stats.msgs}</td></tr></table>
      <div class="pan-sub">Drivers / devices</div>
      <table class="grid"><tr><th>Device</th><th>Driver</th><th>Conn</th><th>Tags</th><th>Scans</th><th>Writes</th><th>Errors</th><th>RTT ms</th><th>Blocks/req</th><th>Last error</th></tr>${drv}</table>
      <div class="pan-sub">Historian</div>
      <table class="grid"><tr><th>URL</th><th>Cached day-chunks</th><th>Upstream requests</th><th>Cache hits</th><th>Errors</th><th>Last error</th></tr>${h}</table>
      <div class="pan-sub">Scripts</div>
      <div>loaded: ${scr.loaded.map(esc).join(", ") || "—"}<br>functions: ${scr.functions.map(esc).join(", ") || "—"}</div>
      ${Object.entries(scr.errors).map(([f, e]) => `<pre class="err">${esc(f)}\n${esc(e)}</pre>`).join("")}`;
  }
}

// =====================================================================================
// Importers
// =====================================================================================
export class ImportPage {
  constructor(root, app) { this.app = app; this.root = root; root.classList.add("tables-page"); }
  render() {
    if (this.done) return;
    this.done = true;
    const out = el("div", { class: "import-report" });
    const card = (title, desc, fields, kind) => {
      const inputs = {};
      const form = el("div", { class: "form grid2" });
      for (const [k, label, ph, type, browse] of fields) {
        const i = type === "check" ? el("input", { type: "checkbox", checked: k === "snapshots" ? "checked" : null }) : el("input", { placeholder: ph || "" });
        inputs[k] = i;
        if (browse) {
          const btn = el("button", { class: "mini", title: "browse this PC", onclick: async () => {
            const r = await pickPath({ title: label, ...browse });
            if (!r) return;
            i.value = Array.isArray(r) ? r.join(" ; ") : r;
            if (browse.onPick) browse.onPick(i.value, inputs);
          } }, "📂 Browse…");
          form.append(el("label", {}, label), el("span", { class: "exprrow" }, i, btn));
        } else form.append(el("label", {}, label), i);
      }
      const run = el("button", { class: "primary", onclick: async () => {
        const params = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.type === "checkbox" ? i.checked : i.value]));
        run.disabled = true; out.innerHTML = `<div class="muted">running ${kind} import… (TIA Portal / GraphWorX may take minutes)</div>`;
        try { const r = await api.post(`/api/import/${kind}`, params); this.report(out, r); this.app.refreshProject(); }
        catch (e) { out.innerHTML = `<pre class="err">${esc(e.message)}</pre>`; }
        run.disabled = false;
      } }, "Import");
      return el("div", { class: "card import-card" }, el("div", { class: "card-h" }, title), el("div", { class: "muted", html: desc }), form, run);
    };
    this.root.append(el("div", { class: "import-grid" },
      card("ICONICS GraphWorX32 (.gdf → .nll)", "Converts every object into editable Noelle objects: shapes, polylines (points recovered), text, buttons, symbols/groups, colors, fonts, dynamics (process points, color/hide/flash/location/rotation/size/animator, pick actions) and OPC tags. Uses GraphWorX32 through COM (GENESIS32 must be installed). Only real raster content (embedded bitmaps, metafiles, pattern brushes) stays an image - no screenshots.", [["files", "Display file(s) or folder", "C:\\...\\Display.gdf ; folder", "text", { mode: "files", ext: [".gdf"] }], ["folder", "Target folder", "displays/imported"], ["tagmap", "OPC tag rewrite (old=new;...)", "Cogent.DataHub.1=opcda:[Cogent.DataHub.1]"], ["snapshots", "Visual identity test (compare with GraphWorX's own rendering - reference only, not imported)", "", "check"]], "gwx"),
      card("Siemens WinCC (TIA Portal Openness)", "Exports HMI screens and HMI tags from a TIA Portal project with Openness (opens a <b>copy</b> of the project), then converts them. You must be in the 'Siemens TIA Openness' group; TIA may ask to allow access the first time.<br><b>Symbolic addressing:</b> tags connected to optimized DBs have no absolute address — they are imported as OPC UA references and listed as warnings.", [["project", "TIA project (.apXX)", "D:\\...\\project.ap17", "text", { mode: "file", ext: [".ap15_1", ".ap16", ".ap17", ".ap18", ".ap19", ".ap20", ".ap21"], onPick: (v, inp) => { const m = v.match(/\.ap(\d+)/i); if (m) inp.version.value = "V" + m[1]; } }], ["device", "HMI device name (empty = all)", "HMI_1"], ["version", "TIA version", "V17"], ["plc", "PLC device mapping (conn=device)", "HMI_Connection_1=S7_1500"], ["folder", "Target folder", "displays/wincc"]], "tia"),
      card("SIMATIC XML (already exported)", "Converts screens/tag tables exported with Openness (SimaticML .xml) or WinCC Unified JSON exports from a folder.", [["path", "Folder with exported XML", "D:\\export", "text", { mode: "folder" }], ["plc", "PLC device mapping (conn=device)", ""], ["folder", "Target folder", "displays/wincc"]], "simatic-xml"),
      card("Weintek EasyBuilder Pro", "Imports the <b>Address Tag Library</b> exported as CSV (EasyBuilder Pro → Library → Address → Export CSV) into tags.csv with Modbus / Siemens / Local HMI addresses mapped to Noelle direct addresses. Screen files (.emtp/.cmtp) are a closed binary format and are not converted.", [["file", "Address tag CSV", "C:\\...\\tags.csv", "text", { mode: "file", ext: [".csv", ".txt"] }], ["devicemap", "Device mapping (EB device=Noelle device)", "Siemens S7-1200=S7_1500;MODBUS RTU=RTU1"], ["prefix", "Tag name prefix", "WT_"]], "weintek"),
    ), out);
  }
  report(out, r) {
    const warn = (r.warnings || []).map((w) => `<li>${esc(w)}</li>`).join("");
    const files = (r.files || []).map((f) => `<li><a href="#" data-p="${esc(f)}">${esc(f)}</a></li>`).join("");
    out.innerHTML = `<div class="card"><div class="card-h">${esc(r.summary || "done")}</div>
      ${files ? `<b>Created</b><ul>${files}</ul>` : ""}${warn ? `<b>Warnings (${r.warnings.length})</b><ul class="warn">${warn}</ul>` : ""}
      ${r.identity ? `<b>Identity check</b><pre>${esc(JSON.stringify(r.identity, null, 2))}</pre>` : ""}</div>`;
    out.querySelectorAll("a[data-p]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); this.app.openInEditor(a.dataset.p); }));
  }
}
