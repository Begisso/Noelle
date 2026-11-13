// Editor side panels: toolbox, explorer, tag browser, inspector (props, dynamics, actions), layers.
import { el, deep, toast, prompt, modal, confirmBox, debounce, formatValue, esc } from "./util.js";
import { TYPES, TOOLBOX, bbox, moveObj, resizeObj } from "./objects.js";
import { api, Q } from "./net.js";
import { loadDoc } from "./view.js";
import { compile } from "./expr.js";
import { buildAddress } from "./dialogs.js";

// =====================================================================================
// Toolbox
// =====================================================================================
export function buildToolbox(host, editor) {
  host.innerHTML = "";
  for (const [title, types] of TOOLBOX) {
    host.append(el("div", { class: "pan-sub" }, title));
    const grid = el("div", { class: "tool-grid" });
    for (const t of types) {
      const b = el("button", { class: "tool", title: `${TYPES[t].label} (click, then drag on the canvas; Shift keeps the tool)`, draggable: "true" }, el("span", { class: "ti" }, TYPES[t].icon), el("span", {}, TYPES[t].label));
      b.onclick = () => editor.setTool(editor.tool === t ? null : t);
      b.ondragstart = (e) => e.dataTransfer.setData("application/x-gf", JSON.stringify({ kind: "tool", type: t }));
      b.dataset.type = t;
      grid.append(b);
    }
    host.append(grid);
  }
  editor.addEventListener("tool", () => host.querySelectorAll(".tool").forEach((b) => b.classList.toggle("on", b.dataset.type === editor.tool)));
}

// =====================================================================================
// Project explorer
// =====================================================================================
export function buildExplorer(host, app) {
  const render = () => {
    const p = app.project;
    host.innerHTML = "";
    const section = (title, items, opts) => {
      const head = el("div", { class: "pan-sub row" }, title, opts.add ? el("button", { class: "mini", title: "new", onclick: opts.add }, "+") : "");
      host.append(head);
      for (const f of items) {
        const it = el("div", { class: "tree-item" + (app.editor?.path === f.path ? " active" : ""), title: f.path, draggable: opts.drag ? "true" : null }, el("span", { class: "ico" }, opts.icon), f.name);
        it.onclick = () => opts.open(f);
        if (opts.drag) it.ondragstart = (e) => e.dataTransfer.setData("application/x-gf", JSON.stringify(opts.drag(f)));
        it.oncontextmenu = (e) => { e.preventDefault(); app.contextMenu(e.clientX, e.clientY, opts.menu(f)); };
        host.append(it);
      }
    };
    const fileMenu = (f) => [
      ["Open", "", () => app.openInEditor(f.path)], ["Run", "", () => app.run(f.path)], ["Open in VS Code", "", () => app.vscode(f.path)],
      ["Rename…", "", async () => { const n = await prompt("New path", f.path); if (n && n !== f.path) { await api.post("/api/file/rename", { path: f.path, to: n }); app.refreshProject(); } }],
      ["Duplicate…", "", async () => { const n = await prompt("Copy to", f.path.replace(".nll", "_copy.nll")); if (n) { const t = await api.text(`/api/file?path=${encodeURIComponent(f.path)}`); await api.put(`/api/file?path=${encodeURIComponent(n)}`, t, true); app.refreshProject(); } }],
      ["Version history…", "", () => app.history(f.path)],
      null, ["Delete", "", async () => { if (await confirmBox(`Delete ${f.path}? (a backup is kept in .history)`)) { await api.post("/api/file/delete", { path: f.path }); app.refreshProject(); } }],
    ];
    section("Displays", p.displays, { icon: "▣", open: (f) => app.openInEditor(f.path), menu: fileMenu, add: () => app.newDisplay("displays") });
    section("Symbols / faceplates", p.symbols, { icon: "◈", open: (f) => app.openInEditor(f.path), menu: fileMenu, add: () => app.newDisplay("symbols"),
      drag: (f) => ({ kind: "symbol", path: f.path }) });
    section("Scripts", p.scripts, { icon: "{}", open: (f) => app.vscode(f.path), menu: (f) => [["Open in VS Code", "", () => app.vscode(f.path)], ["Delete", "", async () => { if (await confirmBox(`Delete ${f.path}?`)) { await api.post("/api/file/delete", { path: f.path }); app.refreshProject(); } }]],
      add: async () => { const n = await prompt("New script (name.py = server Python, name.js = display script)", "scripts/MyScript.py"); if (n) { await api.put(`/api/file?path=${encodeURIComponent(n)}`, n.endsWith(".py") ? PY_TEMPLATE : JS_TEMPLATE, true); app.refreshProject(); app.vscode(n); } } });
    section("Images", p.images, { icon: "🖼", open: () => {}, menu: () => [], drag: (f) => ({ kind: "image", path: f.path }) });
  };
  render();
  return { render };
}

const PY_TEMPLATE = `# Server-side script (hot reloaded on save). IntelliSense: see scripts/hmi.pyi
# hmi.read(ref) / await hmi.write(ref, v) / @hmi.on_change(ref) / @hmi.every(ms) / @hmi.function

@hmi.on_start
def started():
    hmi.log("script started")


@hmi.function
def hello(name="world"):
    """Call from a display: action type 'py', fn 'hello'."""
    hmi.notify(f"Hello {name}!")
    return 42
`;
const JS_TEMPLATE = `// Display script: set the display property "script" to this file.
// Exported functions can be called by actions of type "script".
/** @param {import('./hmi').DisplayContext} ctx */
export function onOpen(ctx) {
  // ctx.subscribe("{{sim:Sine}}", (v) => ctx.set("myRect", { fill: v > 50 ? "red" : "green" }));
}

/** @param {import('./hmi').DisplayContext} ctx */
export function myAction(ctx, args) {
  ctx.toast("clicked " + (ctx.obj ? ctx.obj.id : ""));
}
`;

// =====================================================================================
// Tag browser
// =====================================================================================
export function buildTagBrowser(host, app, { onPick } = {}) {
  host.innerHTML = "";
  const search = el("input", { class: "tb-search", placeholder: "filter… / or type a direct address" });
  const live = el("div", { class: "tb-live" });
  const tree = el("div", { class: "tb-tree" });
  const builder = el("div", { class: "tb-buttons" },
    el("button", { class: "mini", title: "Siemens / Modbus TCP / OPC UA / OPC DA…: type IP + address, test live", onclick: async () => {
      const ref = await buildAddress(app);
      if (ref) { search.value = ref; tryDirect(); onPick && onPick(ref); }
    } }, "⚡ Build address…"),
    el("button", { class: "mini", title: "create a named tag in tags.csv", onclick: async () => {
      const row = await buildAddress(app, { tagMode: true });
      if (!row) return;
      const csv = "Name,Address,Description,Units\n" + [row.Name, row.Address, row.Description, row.Units].map((v) => `"${String(v || "").replace(/"/g, '""')}"`).join(",") + "\n";
      try { await api.post("/api/tags/import?mode=merge", csv, true); toast(`tag ${row.Name} added to tags.csv`); search.value = row.Name; tryDirect(); onPick && onPick(row.Name); }
      catch (e) { toast(e.message, "error"); }
    } }, "＋ New tag…"));
  host.append(builder, search, live, tree);
  let liveRef = null;
  const liveFn = (p) => { live.innerHTML = `<b>${esc(liveRef)}</b> = ${esc(formatValue(p.value))} <span class="q q${p.q >= Q.GOOD ? "g" : "b"}">${p.q >= Q.GOOD ? "GOOD" : "BAD " + p.q}</span>` + (p.meta?.err ? `<div class="err">${esc(p.meta.err)}</div>` : ""); };
  const tryDirect = debounce(() => {
    const v = search.value.trim();
    if (liveRef) { app.conn.unsubscribe(liveRef, liveFn); liveRef = null; live.innerHTML = ""; }
    if (/^[a-z]+:/i.test(v) || /^\w+(\.\w+)+$/.test(v)) {
      liveRef = v;
      live.innerHTML = `<b>${esc(v)}</b> …`;
      app.conn.subscribe(v, liveFn);
      app.conn.rpc("info", { ref: v }).then((i) => { if (i.error && liveRef === v) live.insertAdjacentHTML("beforeend", `<div class="err">${esc(i.error)}</div>`); }).catch(() => {});
      live.draggable = true;
      live.ondragstart = (e) => e.dataTransfer.setData("application/x-gf", JSON.stringify({ kind: "tag", ref: v, name: v }));
      live.onclick = () => onPick && onPick(v);
    }
    filter();
  }, 300);
  search.oninput = tryDirect;
  const filter = () => { const f = search.value.trim().toLowerCase(); tree.querySelectorAll(".tb-leaf").forEach((n) => { n.style.display = !f || n.textContent.toLowerCase().includes(f) ? "" : "none"; }); };

  const leaf = (item) => {
    const n = el("div", { class: "tb-leaf", draggable: "true", title: item.ref + (item.desc ? "\n" + item.desc : "") }, el("span", { class: "ico" }, "●"), item.name, item.desc ? el("small", {}, " " + item.desc) : "");
    n.ondragstart = (e) => e.dataTransfer.setData("application/x-gf", JSON.stringify({ kind: "tag", ref: item.ref, name: item.name, units: item.units }));
    n.onclick = () => { search.value = item.ref; tryDirect(); onPick && onPick(item.ref); };
    return n;
  };
  const branch = (label, load) => {
    const head = el("div", { class: "tb-branch" }, el("span", { class: "arrow" }, "▸"), label);
    const kids = el("div", { class: "tb-kids", style: { display: "none" } });
    let loaded = false;
    head.onclick = async () => {
      const open = kids.style.display === "none";
      kids.style.display = open ? "" : "none";
      head.querySelector(".arrow").textContent = open ? "▾" : "▸";
      if (open && !loaded) {
        loaded = true;
        kids.append(el("div", { class: "muted" }, "loading…"));
        try {
          const items = await load();
          kids.innerHTML = "";
          virtualList(kids, items, (it) => (it.branch ? branch(it.name, () => api.get(`/api/browse?src=${encodeURIComponent(it.src || head.dataset.src)}&path=${encodeURIComponent(it.path || "")}`).then((r) => r.map((x) => ({ ...x, src: it.src || head.dataset.src })))) : leaf(it)));
          filter();
        } catch (e) { kids.innerHTML = ""; kids.append(el("div", { class: "err" }, e.message)); loaded = false; }
      }
    };
    return el("div", {}, head, kids);
  };
  api.get("/api/browse").then((roots) => {
    for (const r of roots) {
      const b = branch(r.name, () => api.get(`/api/browse?src=${encodeURIComponent(r.src)}`).then((list) => list.map((x) => ({ ...x, src: r.src }))));
      b.firstChild.dataset.src = r.src;
      tree.append(b);
    }
    tree.append(el("div", { class: "tb-help", html: DIRECT_HELP }));
  }).catch((e) => tree.append(el("div", { class: "err" }, e.message)));
}

const DIRECT_HELP = `<details><summary>Direct addressing syntax</summary><pre>
sim:Sine(0,100,60)   sim:Ramp  sim:Bool(4)  sim:Walk
mem:Name  mem:Name=10            sys:Time  sys:AlarmsActive
modbus:PLC1/40001:F              modbus:[10.0.0.5:502,1]40001
   0xxxx coil 1xxxx DI 3xxxx IR 4xxxx HR, .b bit,
   :F :D :DI :DU :U :I64 :S20 :SW(word swap) @1000(scan)
s7:PLC2/DB10.DBD4                s7:[192.168.0.1,0,1]M10.2
   DBX/DBB/DBW/DBD, I/E Q/A M  B/W/D, :DINT :WORD :STRING20
opcua:UA/ns=2;s=Channel1.Tag     opcua:[opc.tcp://h:4840]ns=2;i=5
opcda:[Cogent.DataHub.1]default:Point
dh:[127.0.0.1:4502]default:Point (DataHub TCP API)
hist:&lt;description&gt;            last value from the historian
Tank1.Level                      tag database (tags.csv)
Aliases: &lt;#Name#&gt; anywhere, e.g. {{&lt;#Pump#&gt;.Run}}</pre></details>`;

/** Simple virtualized list: renders only rows in view (thousands of OPC items stay fast). */
function virtualList(host, items, make) {
  if (items.length < 300) { items.forEach((it) => host.append(make(it))); return; }
  const rowH = 22, box = el("div", { class: "vlist", style: { height: Math.min(440, items.length * rowH) + "px" } });
  const inner = el("div", { style: { height: items.length * rowH + "px", position: "relative" } });
  box.append(inner);
  host.append(el("div", { class: "muted" }, `${items.length} items`), box);
  let last = -1;
  const draw = () => {
    const first = Math.max(0, Math.floor(box.scrollTop / rowH) - 10);
    if (first === last) return;
    last = first;
    inner.innerHTML = "";
    const n = Math.ceil(box.clientHeight / rowH) + 20;
    for (let i = first; i < Math.min(items.length, first + n); i++) {
      const r = make(items[i]);
      r.style.position = "absolute"; r.style.top = i * rowH + "px"; r.style.left = 0; r.style.right = 0;
      inner.append(r);
    }
  };
  box.addEventListener("scroll", () => requestAnimationFrame(draw));
  setTimeout(draw);
}

export async function pickTag(app, title = "Select tag") {
  let picked = null;
  const host = el("div", { class: "tb-modal" });
  const p = modal({ title, body: host, width: 560, buttons: [["Use", () => picked, "primary"], ["Cancel", null]] });
  buildTagBrowser(host, app, { onPick: (r) => (picked = r) });
  return p;
}

// =====================================================================================
// Inspector
// =====================================================================================
export function buildInspector(host, editor, app) {
  const previews = [];
  const clearPreviews = () => { for (const [ref, fn] of previews) app.conn.unsubscribe(ref, fn); previews.length = 0; };
  const render = () => {
    clearPreviews();
    host.innerHTML = "";
    const sel = editor.selected;
    if (!editor.doc) return;
    if (!sel.length) { displayProps(host, editor, app); return; }
    const o = sel[0], multi = sel.length > 1;
    host.append(el("div", { class: "pan-sub" }, multi ? `${sel.length} objects` : `${TYPES[o.type]?.label || o.type}  ·  ${o.id}`));
    const set = (key, val) => editor.edit(() => sel.forEach((x) => { if (val === "" || val === null || val === undefined) delete x[key]; else x[key] = val; }), "prop");
    const form = el("div", { class: "form grid2" });
    host.append(form);
    if (!multi) {
      field(form, "Name / id", "text", o.id, (v) => v && editor.edit(() => { o.id = v; editor.sel = new Set([v]); }, "rename"));
      const b = bbox(o);
      const geo = (k, label) => field(form, label, "num", Math.round(b[k] * 10) / 10, (v) => editor.edit(() => {
        const nb = { ...b, [k]: +v };
        if (k === "x" || k === "y") moveObj(o, nb.x - b.x, nb.y - b.y);
        else resizeObj(o, b, nb);
      }, "geometry"));
      geo("x", "X"); geo("y", "Y"); geo("w", "Width"); geo("h", "Height");
      field(form, "Rotation °", "num", o.rot || 0, (v) => set("rot", +v || null));
    }
    for (const [key, label, kind] of TYPES[o.type]?.props || []) {
      if (multi && !sel.every((x) => x.type === o.type) && !["fill", "stroke", "sw", "opacity", "color", "fs", "font"].includes(key)) continue;
      if (kind === "pens") { pensEditor(host, o, editor, app); continue; }
      if (kind === "histpens") { histPensEditor(host, o, editor, app); continue; }
      if (kind === "aliases") { symbolAliasEditor(host, o, editor); continue; }
      const f = field(form, label, kind, o[key], (v) => set(key, kind === "num" ? (v === "" ? "" : +v) : v), app);
      if (kind === "expr" || kind === "tag") addPreview(f, kind === "tag" ? (o[key] ? `{{${o[key]}}}` : "") : o[key]);
    }
    field(form, "Tooltip", "text", o.tooltip || "", (v) => set("tooltip", v));
    if (!multi) { dynamicsEditor(host, o, editor, app, addPreview); actionsEditor(host, o, editor, app); }
  };
  const addPreview = (fieldEl, src) => {
    if (!src) return;
    const c = compile(src);
    if (!c.refs.length) return;
    const sp = el("span", { class: "preview" });
    fieldEl.append(sp);
    const upd = () => {
      let q = 192; const vals = c.refs.map((r) => { const p = app.conn.points.get(r); if (!p || p.q < q) q = p ? p.q : 8; return p?.value; });
      let v; try { v = c.fn(vals); } catch (e) { v = "err"; }
      sp.textContent = "= " + formatValue(v);
      sp.className = "preview " + (q >= Q.GOOD ? "ok" : "bad");
    };
    for (const r of c.refs) { const fn = () => upd(); app.conn.subscribe(r, fn); previews.push([r, fn]); }
  };
  editor.addEventListener("selection", render);
  editor.addEventListener("change", (e) => { if (!["move", "nudge", "snap"].includes(e.detail)) render(); });
  return { render };
}

function field(form, label, kind, value, onchange, app) {
  const wrap = el("div", { class: "f" });
  let inp;
  const commit = () => onchange(inp.type === "checkbox" ? inp.checked : inp.value);
  if (kind === "bool") { inp = el("input", { type: "checkbox" }); inp.checked = !!value; inp.onchange = commit; }
  else if (kind?.startsWith("select:")) { inp = el("select", {}, kind.slice(7).split("|").map((o) => el("option", { value: o, selected: o === value ? "selected" : null }, o))); inp.onchange = commit; }
  else if (kind === "color") {
    inp = el("input", { type: "text", value: value ?? "", class: "colortext" });
    const pick = el("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#000000" });
    pick.oninput = () => { inp.value = pick.value; };
    pick.onchange = commit;
    inp.onchange = commit;
    const sw = el("span", { class: "colorrow" }, pick, inp, el("button", { class: "mini", title: "none / transparent", onclick: () => { inp.value = "none"; commit(); } }, "∅"));
    form.append(el("label", {}, label), sw);
    return sw;
  } else if (kind === "area") { inp = el("textarea", { rows: 2 }); inp.value = value ?? ""; inp.onchange = commit; }
  else {
    inp = el("input", { type: kind === "num" ? "number" : "text", value: value ?? "", step: "any" });
    inp.onchange = commit;
    if (kind === "expr" || kind === "tag" || kind === "image" || kind === "symbol" || kind === "display") {
      const row = el("span", { class: "exprrow" }, inp, el("button", { class: "mini", title: "browse", onclick: async () => {
        let r = null;
        if (kind === "expr" || kind === "tag") { r = await pickTag(app); if (r && kind === "expr") r = `{{${r}}}`; }
        else r = await pickFile(app, kind === "image" ? "images" : kind === "symbol" ? "symbols" : "displays");
        if (r) { inp.value = kind === "expr" && inp.value && inp.value !== r && !inp.value.startsWith("{{") ? inp.value + " " + r : r; commit(); }
      } }, "…"));
      form.append(el("label", {}, label), row);
      return row;
    }
  }
  form.append(el("label", {}, label), inp);
  void wrap;
  return inp.parentElement === form ? inp : inp;
}

async function pickFile(app, folder) {
  const list = folder === "images" ? app.project.images : folder === "symbols" ? app.project.symbols : app.project.displays;
  const sel = el("select", { size: 12, style: { width: "100%" } }, list.map((f) => el("option", { value: f.path }, f.path)));
  return modal({ title: `Select ${folder.slice(0, -1)}`, body: sel, buttons: [["OK", () => sel.value, "primary"], ["Cancel", null]] });
}

// ---------------------------------------------------------------- display properties
function displayProps(host, editor, app) {
  const d = editor.doc;
  host.append(el("div", { class: "pan-sub" }, "Display properties"));
  const form = el("div", { class: "form grid2" });
  host.append(form);
  const set = (fn) => editor.edit(fn, "display");
  field(form, "Name", "text", d.name, (v) => set(() => (d.name = v)));
  field(form, "Width", "num", (d.size || [1280, 720])[0], (v) => set(() => (d.size = [+v, (d.size || [1280, 720])[1]])));
  field(form, "Height", "num", (d.size || [1280, 720])[1], (v) => set(() => (d.size = [(d.size || [1280, 720])[0], +v])));
  field(form, "Background", "color", d.background || "#2b2f36", (v) => set(() => (d.background = v)));
  field(form, "Grid", "num", editor.grid, (v) => { editor.grid = Math.max(1, +v || 10); d.grid = editor.grid; editor.drawOverlay(); });
  field(form, "Display script (.js)", "text", d.script || "", (v) => set(() => { if (v) d.script = v; else delete d.script; }));
  host.append(el("div", { class: "pan-sub" }, "Local aliases (defaults)"));
  kvEditor(host, d.aliases || {}, (obj) => set(() => (d.aliases = obj)));
  host.append(el("div", { class: "pan-sub" }, "On open actions"));
  actionsList(host, d.onOpen || [], (list) => set(() => (d.onOpen = list)), app, ["open", "write", "script", "py", "aliasSet"]);
  host.append(el("div", { class: "hint", html: "<b>Tips</b><br>Drag tags from the tag browser onto the canvas (creates label+value) or onto an object (binds it).<br>Ctrl+wheel zoom · Space+drag pan · G snap · Alt+drag duplicate · Shift+drag constrain · Ctrl+drag no snap · Ctrl+dblclick on a line adds a point · Alt+click a point removes it · F5 test run · Ctrl+S save" }));
}

function kvEditor(host, obj, onchange) {
  const box = el("div", { class: "kv" });
  const rows = Object.entries(obj);
  const redraw = () => {
    box.innerHTML = "";
    rows.forEach(([k, v], i) => {
      const ki = el("input", { value: k, placeholder: "alias" }), vi = el("input", { value: v, placeholder: "value" });
      const upd = () => { rows[i] = [ki.value, vi.value]; onchange(Object.fromEntries(rows.filter((r) => r[0]))); };
      ki.onchange = upd; vi.onchange = upd;
      box.append(el("div", { class: "kvrow" }, ki, vi, el("button", { class: "mini", onclick: () => { rows.splice(i, 1); onchange(Object.fromEntries(rows)); } }, "×")));
    });
    box.append(el("button", { class: "mini wide", onclick: () => { rows.push(["", ""]); redraw(); } }, "+ alias"));
  };
  redraw();
  host.append(box);
}

async function symbolAliasEditor(host, o, editor) {
  host.append(el("div", { class: "pan-sub" }, "Symbol aliases"));
  const box = el("div", { class: "form grid2" });
  host.append(box);
  let declared = {};
  if (o.src) { try { declared = (await loadDoc(o.src)).aliases || {}; } catch { /* ignore */ } }
  const keys = [...new Set([...Object.keys(declared), ...Object.keys(o.aliases || {})])];
  if (!keys.length) box.append(el("div", { class: "muted" }, "symbol declares no aliases"));
  for (const k of keys) {
    const inp = el("input", { value: (o.aliases || {})[k] ?? "", placeholder: declared[k] ? `default: ${declared[k]}` : "" });
    inp.onchange = () => editor.edit(() => { o.aliases = { ...(o.aliases || {}), [k]: inp.value }; }, "alias");
    box.append(el("label", {}, `<#${k}#>`), inp);
  }
}

// ---------------------------------------------------------------- pens
function pensEditor(host, o, editor, app) {
  host.append(el("div", { class: "pan-sub" }, "Pens"));
  const box = el("div", { class: "pens" });
  host.append(box);
  (o.pens || []).forEach((p, i) => {
    const ex = el("input", { value: p.expr || "", placeholder: "{{tag}} or expression" });
    const nm = el("input", { value: p.name || "", placeholder: "name" });
    const co = el("input", { type: "color", value: p.color || "#3fa7ff" });
    const upd = () => editor.edit(() => { o.pens[i] = { ...p, expr: ex.value, name: nm.value, color: co.value }; }, "pen");
    ex.onchange = upd; nm.onchange = upd; co.onchange = upd;
    box.append(el("div", { class: "penrow" }, co, ex, nm,
      el("button", { class: "mini", onclick: async () => { const r = await pickTag(app); if (r) { ex.value = `{{${r}}}`; if (!nm.value) nm.value = r; upd(); } } }, "…"),
      el("button", { class: "mini", onclick: () => editor.edit(() => o.pens.splice(i, 1), "pen") }, "×")));
  });
  box.append(el("button", { class: "mini wide", onclick: () => editor.edit(() => (o.pens = [...(o.pens || []), { expr: "", name: "", color: "" }]), "pen") }, "+ pen"));
}

function histPensEditor(host, o, editor, app) {
  host.append(el("div", { class: "pan-sub" }, "Historian pens"));
  const box = el("div", { class: "pens" });
  host.append(box);
  (o.pens || []).forEach((p, i) => {
    const d = el("input", { value: p.desc || "", placeholder: "historian description" });
    const co = el("input", { type: "color", value: p.color || "#3fa7ff" });
    const upd = () => editor.edit(() => { o.pens[i] = { ...p, desc: d.value, color: co.value }; }, "pen");
    d.onchange = upd; co.onchange = upd;
    box.append(el("div", { class: "penrow" }, co, d, el("button", { class: "mini", onclick: () => editor.edit(() => o.pens.splice(i, 1), "pen") }, "×")));
  });
  box.append(el("button", { class: "mini wide", onclick: async () => {
    const r = await app.pickHistorian();
    if (r && r.length) editor.edit(() => (o.pens = [...(o.pens || []), ...r.map((desc) => ({ desc, name: desc }))]), "pen");
  } }, "+ pens from historian…"));
}

// ---------------------------------------------------------------- dynamics
const DYN_KINDS = {
  fill: "Fill color", stroke: "Line color", color: "Text color", visible: "Visibility", blink: "Blink", text: "Text value",
  x: "Move X", y: "Move Y", rot: "Rotate", scale: "Size", level: "Fill level (%)", opacity: "Opacity", enabled: "Enabled",
  animate: "Animate frames (groups)",
};
function dynamicsEditor(host, o, editor, app, addPreview) {
  host.append(el("div", { class: "pan-sub row" }, "Dynamics", el("select", { class: "mini", onchange: (e) => {
    const k = e.target.value; e.target.value = "";
    if (!k) return;
    const defs = { fill: { expr: "", type: "bool", on: "#2ee66b", off: "#59616c" }, stroke: { expr: "", type: "bool", on: "#ff0000", off: "#202428" }, color: { expr: "", type: "range", stops: [[0, "#e6e9ee"], [80, "#f0b429"], [90, "#e5484d"]] },
      visible: { expr: "" }, blink: { expr: "" }, text: { expr: "", fmt: "0.0" }, x: { expr: "", in: [0, 100], out: [0, 200] }, y: { expr: "", in: [0, 100], out: [0, 200] },
      rot: { expr: "", in: [0, 100], out: [0, 360] }, scale: { expr: "", in: [0, 100], out: [0, 1], anchor: "bottom", dir: "v" }, level: { expr: "", in: [0, 100], dir: "up", color: "#3fa7ff" }, opacity: { expr: "", in: [0, 100], out: [0.2, 1] }, enabled: { expr: "" }, animate: { expr: "", ms: 500, offFrame: 0 } };
    editor.edit(() => { o.dyn = { ...(o.dyn || {}), [k]: defs[k] }; }, "dyn");
  } }, el("option", { value: "" }, "+ add"), Object.entries(DYN_KINDS).map(([k, v]) => el("option", { value: k }, v)))));
  for (const [k, d] of Object.entries(o.dyn || {})) {
    const card = el("div", { class: "card" });
    card.append(el("div", { class: "card-h" }, DYN_KINDS[k] || k, el("button", { class: "mini", title: "remove", onclick: () => editor.edit(() => { delete o.dyn[k]; if (!Object.keys(o.dyn).length) delete o.dyn; }, "dyn") }, "×")));
    const form = el("div", { class: "form grid2" });
    card.append(form);
    const set = (key, v) => editor.edit(() => { o.dyn[k] = { ...o.dyn[k], [key]: v }; }, "dyn");
    const f = field(form, "Expression", "expr", d.expr ?? (d.tag ? `{{${d.tag}}}` : ""), (v) => set("expr", v), app);
    addPreview(f, d.expr);
    if (["fill", "stroke", "color"].includes(k)) {
      field(form, "Mode", "select:bool|range|map|blend", d.type || "bool", (v) => set("type", v));
      if ((d.type || "bool") === "bool") { field(form, "True color", "color", d.on, (v) => set("on", v)); field(form, "False color", "color", d.off, (v) => set("off", v)); }
      if (d.type === "blend") { field(form, "Min", "num", d.min ?? 0, (v) => set("min", +v)); field(form, "Max", "num", d.max ?? 100, (v) => set("max", +v)); field(form, "From color", "color", d.from, (v) => set("from", v)); field(form, "To color", "color", d.to, (v) => set("to", v)); }
      if (d.type === "range") field(form, "Stops ≥value:color;…", "text", (d.stops || []).map((s) => s.join(":")).join(";"), (v) => set("stops", v.split(";").filter(Boolean).map((s) => { const [a, ...b] = s.split(":"); return [+a, b.join(":")]; })));
      if (d.type === "map") field(form, "Map value:color;…", "text", Object.entries(d.map || {}).map((e) => e.join(":")).join(";"), (v) => set("map", Object.fromEntries(v.split(";").filter(Boolean).map((s) => { const [a, ...b] = s.split(":"); return [a, b.join(":")]; }))));
      if (d.type === "map" || d.type === "range") field(form, "Default color", "color", d.default, (v) => set("default", v));
      field(form, "Bad quality color", "color", d.bad || "", (v) => set("bad", v));
    }
    if (k === "visible") field(form, "Hide when true", "bool", !!d.invert, (v) => set("invert", v));
    if (k === "blink") field(form, "Fast", "bool", !!d.fast, (v) => set("fast", v));
    if (k === "text") { field(form, "Format", "text", d.fmt || "", (v) => set("fmt", v)); field(form, "Units", "text", d.units || "", (v) => set("units", v)); }
    if (["x", "y", "rot", "scale", "opacity", "level"].includes(k)) {
      field(form, "Input min,max", "text", (d.in || [0, 100]).join(","), (v) => set("in", v.split(",").map(Number)));
      if (k !== "level") field(form, "Output min,max", "text", (d.out || [0, 100]).join(","), (v) => set("out", v.split(",").map(Number)));
    }
    if (k === "rot") { field(form, "Pivot X (0..1)", "num", d.cx ?? 0.5, (v) => set("cx", +v)); field(form, "Pivot Y (0..1)", "num", d.cy ?? 0.5, (v) => set("cy", +v)); }
    if (k === "scale") { field(form, "Anchor", "select:bottom|top|center|left|right", d.anchor || "bottom", (v) => set("anchor", v)); field(form, "Direction", "select:v|h|both", d.dir || "v", (v) => set("dir", v)); }
    if (k === "animate") { field(form, "Frame time (ms)", "num", d.ms ?? 500, (v) => set("ms", +v)); field(form, "Frame when off", "num", d.offFrame ?? 0, (v) => set("offFrame", +v)); }
    if (k === "level") { field(form, "Direction", "select:up|down|left|right", d.dir || "up", (v) => set("dir", v)); field(form, "Level color", "color", d.color, (v) => set("color", v)); }
    host.append(card);
  }
}

// ---------------------------------------------------------------- actions
const ACTION_TYPES = { write: "Write value", toggle: "Toggle", input: "Data entry", open: "Open display / popup", close: "Close popup", back: "Back", script: "Display script (JS)", py: "Server script (Python)", aliasSet: "Switch alias set", ack: "Acknowledge alarms", recipe: "Recipe", historian: "Open historian", url: "Open URL", login: "Log in", logout: "Log out" };
function actionsEditor(host, o, editor, app) {
  host.append(el("div", { class: "pan-sub" }, "Actions"));
  actionsList(host, o.actions || [], (list) => editor.edit(() => { if (list.length) o.actions = list; else delete o.actions; }, "actions"), app);
}

function actionsList(host, list, onchange, app, allowed) {
  list = deep(list);
  const box = el("div", {});
  host.append(box);
  list.forEach((a, i) => {
    const card = el("div", { class: "card" });
    card.append(el("div", { class: "card-h" }, ACTION_TYPES[a.type] || a.type, el("button", { class: "mini", onclick: () => { list.splice(i, 1); onchange(list); } }, "×")));
    const form = el("div", { class: "form grid2" });
    const set = (k, v) => { if (v === "" || v == null) delete a[k]; else a[k] = v; onchange(list); };
    field(form, "Trigger", "select:click|down|up|dblclick", a.on || "click", (v) => set("on", v));
    if (["write", "toggle", "input"].includes(a.type)) field(form, "Tag", "tag", a.tag, (v) => set("tag", v), app);
    if (a.type === "write") field(form, "Value (expr)", "text", a.value ?? "1", (v) => set("value", v));
    if (a.type === "input") { field(form, "Min", "num", a.min ?? "", (v) => set("min", v)); field(form, "Max", "num", a.max ?? "", (v) => set("max", v)); field(form, "Prompt", "text", a.prompt || "", (v) => set("prompt", v)); }
    if (a.type === "open") {
      field(form, "Display", "display", a.display, (v) => set("display", v), app);
      field(form, "Target", "select:main|popup|window", a.target || "main", (v) => set("target", v));
      field(form, "Popup title", "text", a.title || "", (v) => set("title", v));
      field(form, "Aliases A=B;C=D", "text", Object.entries(a.aliases || {}).map((e) => e.join("=")).join(";"), (v) => set("aliases", Object.fromEntries(v.split(";").filter(Boolean).map((s) => s.split("=").map((x) => x.trim())))));
    }
    if (a.type === "script" || a.type === "py") { field(form, "Function", "text", a.fn || "", (v) => set("fn", v)); field(form, "Args (JSON)", "text", a.args ? JSON.stringify(a.args) : "", (v) => { try { set("args", v ? JSON.parse(v) : null); } catch { toast("invalid JSON", "error"); } }); }
    if (a.type === "aliasSet") field(form, "Alias set", "text", a.set || "", (v) => set("set", v));
    if (a.type === "recipe") { field(form, "Recipe", "text", a.name || "", (v) => set("name", v)); field(form, "Mode", "select:apply|save", a.mode || "apply", (v) => set("mode", v)); }
    if (a.type === "url") field(form, "URL", "text", a.href || "", (v) => set("href", v));
    if (a.type === "historian") field(form, "Pens (desc;desc)", "text", (a.pens || []).join(";"), (v) => set("pens", v.split(";").filter(Boolean)));
    field(form, "Confirm text", "text", a.confirm || "", (v) => set("confirm", v));
    field(form, "Required role", "select:|viewer|operator|engineer|admin", a.role || "", (v) => set("role", v));
    card.append(form);
    box.append(card);
  });
  const add = el("select", { class: "mini wide", onchange: (e) => { const t = e.target.value; if (!t) return; list.push({ on: "click", type: t, ...(t === "write" ? { value: "1" } : {}) }); onchange(list); } },
    el("option", { value: "" }, "+ add action"), Object.entries(ACTION_TYPES).filter(([k]) => !allowed || allowed.includes(k)).map(([k, v]) => el("option", { value: k }, v)));
  box.append(add);
}

// =====================================================================================
// Layers
// =====================================================================================
export function buildLayers(host, editor) {
  const filter = el("input", { placeholder: "filter objects / tags…", class: "tb-search" });
  const list = el("div", { class: "layers" });
  host.append(filter, list);
  const render = () => {
    list.innerHTML = "";
    if (!editor.doc) return;
    const f = filter.value.toLowerCase();
    [...editor.objects].reverse().forEach((o) => {
      const text = `${o.id} ${o.type} ${JSON.stringify(o.expr || o.tag || o.text || o.dyn || "")}`.toLowerCase();
      if (f && !text.includes(f)) return;
      const row = el("div", { class: "layer" + (editor.sel.has(o.id) ? " sel" : "") },
        el("span", { class: "ico" }, TYPES[o.type]?.icon || "?"), el("span", { class: "nm" }, o.id), el("small", {}, o.type),
        el("button", { class: "mini" + (o.hidden ? " off" : ""), title: "hide in editor", onclick: (e) => { e.stopPropagation(); editor.edit(() => { o.hidden = !o.hidden; if (!o.hidden) delete o.hidden; }, "hide"); } }, "👁"),
        el("button", { class: "mini" + (o.locked ? " on" : ""), title: "lock", onclick: (e) => { e.stopPropagation(); editor.edit(() => { o.locked = !o.locked; if (!o.locked) delete o.locked; }, "lock"); } }, "🔒"));
      row.onclick = (e) => editor.select(o.id, e.shiftKey || e.ctrlKey);
      list.append(row);
    });
  };
  filter.oninput = render;
  editor.addEventListener("selection", render);
  editor.addEventListener("change", render);
  return { render };
}
