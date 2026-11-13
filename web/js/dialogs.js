// Shared dialogs: server-side file/folder picker and the tag address builder.
import { el, modal, toast, formatValue, esc, debounce } from "./util.js";
import { api, Q } from "./net.js";

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

// =====================================================================================
// File / folder picker (lists the SERVER's disks - the server runs on this PC)
// mode: "file" | "files" | "folder";  ext: [".gdf", ...]
// =====================================================================================
export function pickPath({ title = "Select", mode = "file", ext = [], start = "" } = {}) {
  const key = "noelle.lastDir." + (ext.join(",") || mode);
  let cur = start || lsGet(key) || "";
  const chosen = new Set();
  const pathIn = el("input", { class: "fp-path", placeholder: "path - type and press Enter" });
  const list = el("div", { class: "fp-list" });
  const info = el("div", { class: "muted fp-info" });
  const filter = el("input", { class: "fp-filter", placeholder: "filter…" });
  const up = el("button", { title: "up" }, "⬆");
  const home = el("button", { title: "drives & places" }, "🖴");
  let data = null;
  const render = () => {
    list.innerHTML = "";
    if (!data) return;
    const f = filter.value.toLowerCase();
    for (const d of data.dirs) {
      if (f && !d.name.toLowerCase().includes(f)) continue;
      const row = el("div", { class: "fp-row dir", title: d.path }, el("span", { class: "ico" }, d.kind === "drive" ? "🖴" : d.kind === "place" ? "★" : "📁"), d.name);
      row.ondblclick = () => go(d.path);
      row.onclick = () => { if (mode === "folder") { list.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel")); row.classList.add("sel"); pathIn.value = d.path; } else go(d.path); };
      list.append(row);
    }
    for (const fi of data.files) {
      if (f && !fi.name.toLowerCase().includes(f)) continue;
      const cb = mode === "files" ? el("input", { type: "checkbox" }) : null;
      if (cb) cb.checked = chosen.has(fi.path);
      const row = el("div", { class: "fp-row file" + (chosen.has(fi.path) ? " sel" : ""), title: fi.path }, cb || el("span", { class: "ico" }, "📄"), el("span", { class: "nm" }, fi.name),
        el("small", {}, `${(fi.size / 1024).toFixed(0)} KB · ${new Date(fi.mtime * 1000).toLocaleDateString()}`));
      row.onclick = (e) => {
        if (mode === "files") { if (chosen.has(fi.path)) chosen.delete(fi.path); else chosen.add(fi.path); if (e.target !== cb && cb) cb.checked = chosen.has(fi.path); row.classList.toggle("sel", chosen.has(fi.path)); }
        else { chosen.clear(); chosen.add(fi.path); list.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel")); row.classList.add("sel"); }
        info.textContent = mode === "files" ? `${chosen.size} file(s) selected` : fi.path;
      };
      row.ondblclick = () => { if (mode === "file") okBtn && okBtn.click(); };
      list.append(row);
    }
    if (!data.dirs.length && !data.files.length) list.append(el("div", { class: "muted" }, "empty"));
  };
  const go = async (p) => {
    try {
      data = await api.get(`/api/fs/list?path=${encodeURIComponent(p)}&ext=${encodeURIComponent(ext.join(","))}`);
      cur = data.path; pathIn.value = cur; filter.value = "";
      if (cur) lsSet(key, cur);
      up.disabled = !cur;
      render();
      info.textContent = mode === "folder" ? "select a folder (or use the current one)" : `${data.files.length} matching file(s)` + (ext.length ? ` (${ext.join(", ")})` : "");
    } catch (e) { toast(e.message, "error"); if (p) go(""); }
  };
  up.onclick = () => go(data?.parent || "");
  home.onclick = () => go("");
  pathIn.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); go(pathIn.value.trim()); } };
  filter.oninput = debounce(render, 120);
  let okBtn = null;
  const result = () => {
    if (mode === "folder") return pathIn.value.trim() || cur || null;
    if (mode === "files") {
      if (chosen.size) return [...chosen];
      return cur ? [cur] : null;          // nothing ticked -> whole folder
    }
    return [...chosen][0] || null;
  };
  const p = modal({
    title, width: 720,
    body: el("div", { class: "fp" }, el("div", { class: "fp-bar" }, home, up, pathIn), filter, list, info,
      mode === "files" ? el("div", { class: "muted" }, "Tick files, or press 'Use' with nothing ticked to import the whole folder.") : ""),
    buttons: [[mode === "folder" ? "Use this folder" : "Use", result, "primary"], ["Cancel", null]],
    onOpen: (box) => { okBtn = box.querySelector(".modal-buttons .primary"); },
  });
  go(cur);
  return p;
}

// =====================================================================================
// Address builder: pick protocol, type IP + address, test live, use / add to tag database
// =====================================================================================
const S7_TYPES = ["BOOL", "BYTE", "INT", "WORD", "DINT", "DWORD", "REAL", "LREAL", "TIME", "STRING"];
const MB_TYPES = [["INT16", ""], ["UINT16", "U"], ["INT32", "DI"], ["UINT32", "DU"], ["FLOAT32", "F"], ["FLOAT64", "D"], ["INT64", "I64"], ["BIT of register", "BIT"], ["STRING", "S"]];
const PROTOCOLS = [
  ["s7", "Siemens S7 (S7-300/400/1200/1500, ISO-on-TCP)"],
  ["modbus", "Modbus TCP"],
  ["modbus-rtu-tcp", "Modbus RTU over TCP (serial gateway)"],
  ["modbus-serial", "Modbus RTU (COM port)"],
  ["opcua", "OPC UA"],
  ["opcda", "OPC DA (Cogent DataHub, ICONICS, Kepware…)"],
  ["mem", "Memory tag"],
  ["sim", "Simulator"],
];

/** opts: {tagMode: bool -> also ask for Name/Description/Units and add to tags.csv} returns ref | {row} */
export async function buildAddress(app, { tagMode = false, title } = {}) {
  const devices = (app.project?.cfg?.devices || []);
  const v = JSON.parse(lsGet("noelle.addrBuilder") || "{}");
  const st = Object.assign({ proto: "s7", conn: "direct", ip: "192.168.0.1", port: "", rack: 0, slot: 1, unit: 1, com: "COM1", baud: 9600, parity: "8N1",
    area: "DB", db: 1, offset: 0, bit: 0, s7type: "REAL", strlen: 20, table: "4", mbaddr: 1, mbtype: "", mbbit: 0, wordswap: false, byteswap: false, mbstr: 10,
    endpoint: "", nodeid: "ns=3;s=\"DB1\".\"Tag\"", progid: "Cogent.DataHub.1", dahost: "", item: "default:Tag", memname: "MyTag", meminit: "0",
    simfn: "Sine", simargs: "0,100,60", scan: "" }, v);
  const form = el("div", { class: "form grid2 ab-form" });
  const out = el("input", { class: "ab-out", spellcheck: "false" });
  const live = el("div", { class: "tb-live ab-live" });
  const extra = el("div", { class: "form grid2" });
  const nameIn = el("input", { placeholder: "e.g. Tank1.Level" }), descIn = el("input", { placeholder: "description" }), unitsIn = el("input", { placeholder: "units" });
  if (tagMode) extra.append(el("label", {}, "Tag name"), nameIn, el("label", {}, "Description"), descIn, el("label", {}, "Units"), unitsIn);
  let liveRef = null;
  const liveFn = (p) => { live.innerHTML = `<b>${esc(liveRef)}</b> = ${esc(formatValue(p.value))} <span class="q q${p.q >= Q.GOOD ? "g" : "b"}">${p.q >= Q.GOOD ? "GOOD" : p.q === Q.NOT_CONNECTED ? "connecting…" : "BAD " + p.q}</span>` + (p.meta?.err ? `<div class="err">${esc(p.meta.err)}</div>` : ""); };
  const stopLive = () => { if (liveRef) { app.conn.unsubscribe(liveRef, liveFn); liveRef = null; } };
  const test = () => {
    stopLive();
    const ref = out.value.trim();
    if (!ref) return;
    liveRef = ref; live.innerHTML = `<b>${esc(ref)}</b> … connecting`;
    app.conn.subscribe(ref, liveFn);
    setTimeout(() => app.conn.rpc("info", { ref }).then((i) => { if (liveRef === ref && i.error) live.insertAdjacentHTML("beforeend", `<div class="err">${esc(i.error)}</div>`); }).catch(() => {}), 2500);
  };

  const F = (label, key, kind = "text", options) => {
    let inp;
    if (kind === "select") { inp = el("select", {}, options.map((o) => { const [val, lab] = Array.isArray(o) ? o : [o, o]; return el("option", { value: val, selected: String(st[key]) === String(val) ? "selected" : null }, lab); })); }
    else if (kind === "check") { inp = el("input", { type: "checkbox" }); inp.checked = !!st[key]; }
    else inp = el("input", { type: kind === "num" ? "number" : "text", value: st[key] ?? "" });
    inp.oninput = inp.onchange = () => { st[key] = kind === "check" ? inp.checked : kind === "num" ? +inp.value : inp.value; if (["proto", "conn", "area", "table", "mbtype", "s7type"].includes(key)) draw(); else compute(); };
    form.append(el("label", {}, label), inp);
    return inp;
  };
  const devOpts = (type) => [["direct", "Direct - type IP / endpoint"], ...devices.filter((d) => d.type === type || (type === "modbus" && d.type === "modbus")).map((d) => [d.name, `Device: ${d.name} (${d.host || d.endpoint || d.server || ""})`])];

  const compute = () => {
    let ref = "";
    const p = st.proto, direct = st.conn === "direct";
    const scan = st.scan ? `@${st.scan}` : "";
    if (p === "s7") {
      const dev = direct ? `[${st.ip}${st.port ? ":" + st.port : ""},${st.rack},${st.slot}]` : `${st.conn}/`;
      const t = st.s7type, a = st.area;
      let addr;
      if (t === "BOOL") addr = a === "DB" ? `DB${st.db}.DBX${st.offset}.${st.bit}` : `${a}${st.offset}.${st.bit}`;
      else if (t === "STRING") addr = `${a === "DB" ? `DB${st.db}.DBB` : a + "B"}${st.offset}:STRING${st.strlen}`;
      else {
        const size = { BYTE: "B", INT: "W", WORD: "W", DINT: "D", DWORD: "D", REAL: "D", TIME: "D", LREAL: "D" }[t];
        const def = { B: "BYTE", W: "INT", D: "REAL" }[size];
        addr = (a === "DB" ? `DB${st.db}.DB${size}` : `${a}${size}`) + st.offset + (t !== def ? `:${t}` : "");
      }
      ref = `s7:${dev}${addr}${scan}`;
    } else if (p.startsWith("modbus")) {
      let dev;
      if (!direct) dev = `${st.conn}/`;
      else if (p === "modbus-serial") dev = `[${st.com}:${st.baud}:${st.parity},${st.unit}]`;
      else dev = `[${st.ip}:${st.port || 502},${st.unit}${p === "modbus-rtu-tcp" ? ",rtu" : ""}]`;
      const tbl = st.table, n = Math.max(1, +st.mbaddr || 1);
      let addr = tbl + String(n).padStart(n > 9999 ? 5 : 4, "0");
      if (tbl === "3" || tbl === "4") {
        if (st.mbtype === "BIT") addr += `.${st.mbbit}`;
        else if (st.mbtype === "S") addr += `:S${st.mbstr}`;
        else if (st.mbtype) addr += `:${st.mbtype}`;
        if (st.wordswap && st.mbtype !== "BIT") addr += ":SW";
        if (st.byteswap && st.mbtype !== "BIT") addr += ":BS";
      }
      ref = `modbus:${dev}${addr}${scan}`;
    } else if (p === "opcua") {
      const ep = st.endpoint || `opc.tcp://${st.ip}:${st.port || 4840}`;
      ref = direct ? `opcua:[${ep}]${st.nodeid}` : `opcua:${st.conn}/${st.nodeid}`;
    } else if (p === "opcda") {
      ref = direct ? `opcda:[${st.dahost ? st.dahost + "," : ""}${st.progid}]${st.item}` : `opcda:${st.conn}/${st.item}`;
    } else if (p === "mem") ref = `mem:${st.memname}${st.meminit !== "" ? "=" + st.meminit : ""}`;
    else if (p === "sim") ref = `sim:${st.simfn}${st.simargs ? `(${st.simargs})` : ""}`;
    out.value = ref;
    lsSet("noelle.addrBuilder", JSON.stringify(st));
    debTest();
  };
  const debTest = debounce(test, 600);

  const browseBtn = (srcFn, setter) => el("button", { class: "mini", onclick: async () => {
    const src = srcFn();
    const picked = await browseSource(src);
    if (picked) { setter(picked); compute(); }
  } }, "browse…");

  const draw = () => {
    form.innerHTML = "";
    F("Protocol", "proto", "select", PROTOCOLS);
    const p = st.proto;
    if (p === "s7") {
      F("Connection", "conn", "select", devOpts("s7"));
      if (st.conn === "direct") { F("PLC IP address", "ip"); F("Rack", "rack", "num"); F("Slot (300/400: 2, 1200/1500: 1)", "slot", "num"); }
      F("Area", "area", "select", [["DB", "DB - data block"], ["M", "M - memory (merker)"], ["I", "I/E - inputs"], ["Q", "Q/A - outputs"]]);
      if (st.area === "DB") F("DB number", "db", "num");
      F("Byte offset", "offset", "num");
      F("Data type", "s7type", "select", S7_TYPES);
      if (st.s7type === "BOOL") F("Bit (0-7)", "bit", "num");
      if (st.s7type === "STRING") F("Max length", "strlen", "num");
      F("Scan ms (empty = device)", "scan");
      form.append(el("div", { class: "hint ab-hint" }, "S7-1200/1500: allow PUT/GET and turn off 'Optimized block access' for the DB, otherwise use OPC UA."));
    } else if (p.startsWith("modbus")) {
      F("Connection", "conn", "select", devOpts("modbus"));
      if (st.conn === "direct") {
        if (p === "modbus-serial") { F("COM port", "com"); F("Baud", "baud", "num"); F("Format", "parity", "select", ["8N1", "8E1", "8O1", "8N2"]); }
        else { F("IP address", "ip"); F("Port (502)", "port"); }
        F("Unit / slave id", "unit", "num");
      }
      F("Table", "table", "select", [["4", "Holding register (4x, FC3/6/16)"], ["3", "Input register (3x, FC4)"], ["0", "Coil (0x, FC1/5)"], ["1", "Discrete input (1x, FC2)"]]);
      F("Address (1-based)", "mbaddr", "num");
      if (st.table === "3" || st.table === "4") {
        F("Data type", "mbtype", "select", MB_TYPES.map(([l, c]) => [c, l]));
        if (st.mbtype === "BIT") F("Bit (0-15)", "mbbit", "num");
        if (st.mbtype === "S") F("Characters", "mbstr", "num");
        if (st.mbtype !== "BIT" && st.mbtype !== "") { F("Word swap", "wordswap", "check"); }
        F("Byte swap", "byteswap", "check");
      }
      F("Scan ms (empty = device)", "scan");
    } else if (p === "opcua") {
      F("Connection", "conn", "select", devOpts("opcua"));
      if (st.conn === "direct") { F("Server IP", "ip"); F("Port (4840)", "port"); F("…or endpoint URL", "endpoint"); }
      const nid = F("NodeId", "nodeid");
      form.append(el("label", {}, ""), browseBtn(() => st.conn === "direct" ? `opcua:[${st.endpoint || `opc.tcp://${st.ip}:${st.port || 4840}`}]` : st.conn, (r) => { st.nodeid = r.replace(/^opcua:(\[[^\]]*\]|[^/]*\/)/, ""); nid.value = st.nodeid; }));
      form.append(el("div", { class: "hint ab-hint" }, 'S7-1500 OPC UA server: ns=3;s="DB_Name"."Member"'));
    } else if (p === "opcda") {
      F("Connection", "conn", "select", devOpts("opcda"));
      if (st.conn === "direct") { F("Server ProgID", "progid", "select", ["Cogent.DataHub.1", "ICONICS.Simulator.1", "Kepware.KEPServerEX.V6", "OPC.SimaticHMI.CoRtHmiRTm.1", "CoDeSys.OPC.02", st.progid].filter((x, i, a) => a.indexOf(x) === i)); F("Remote host (empty = this PC)", "dahost"); }
      const it = F("Item ID", "item");
      form.append(el("label", {}, ""), browseBtn(() => st.conn === "direct" ? `opcda:[${st.dahost ? st.dahost + "," : ""}${st.progid}]` : st.conn, (r) => { st.item = r.replace(/^opcda:(\[[^\]]*\]|[^/]*\/)/, ""); it.value = st.item; }));
    } else if (p === "mem") { F("Name", "memname"); F("Initial value", "meminit"); }
    else if (p === "sim") { F("Function", "simfn", "select", ["Sine", "Cosine", "Ramp", "Triangle", "Square", "Bool", "Random", "Noise", "Walk", "Count"]); F("Arguments", "simargs"); }
    compute();
  };
  draw();
  const body = el("div", { class: "ab" }, form, el("div", { class: "pan-sub" }, "Address (editable)"), out, live, extra);
  out.oninput = debTest;
  const res = await modal({ title: title || (tagMode ? "New tag" : "Build address"), width: 600, body,
    buttons: [[tagMode ? "Add tag" : "Use", () => out.value.trim(), "primary"], ["Cancel", null]] });
  stopLive();
  if (!res) return null;
  if (!tagMode) return res;
  const name = nameIn.value.trim();
  if (!name) { toast("tag name required", "error"); return null; }
  return { Name: name, Address: res, Description: descIn.value.trim(), Units: unitsIn.value.trim() };
}

/** Browse a device/driver source (device name, "opcua:[endpoint]", "opcda:[ProgID]") and return a tag ref. */
async function browseSource(src) {
  const list = el("div", { class: "fp-list" });
  const crumbs = el("div", { class: "muted" });
  let picked = null;
  const stack = [];
  const load = async (path) => {
    list.innerHTML = "<div class='muted'>loading… (first connection can take a few seconds)</div>";
    crumbs.textContent = path || "/";
    try {
      const items = await api.get(`/api/browse?src=${encodeURIComponent(src)}&path=${encodeURIComponent(path || "")}`);
      list.innerHTML = "";
      if (stack.length) { const up = el("div", { class: "fp-row dir" }, "⬆ .."); up.onclick = () => { stack.pop(); load(stack[stack.length - 1] || ""); }; list.append(up); }
      for (const it of items) {
        const drill = () => { stack.push(it.path); load(it.path); };
        const row = el("div", { class: "fp-row " + (it.ref ? "file" : "dir") }, el("span", { class: "ico" }, it.ref ? "●" : "📁"), el("span", { class: "nm" }, it.name),
          it.ref && it.branch ? el("button", { class: "mini", title: "show children", onclick: (e) => { e.stopPropagation(); drill(); } }, "▸") : "");
        row.onclick = it.ref ? () => { picked = it.ref; list.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel")); row.classList.add("sel"); } : drill;
        list.append(row);
      }
      if (!items.length) list.append(el("div", { class: "muted" }, "nothing here"));
    } catch (e) { list.innerHTML = ""; list.append(el("div", { class: "err" }, e.message)); }
  };
  const p = modal({ title: `Browse ${src}`, width: 560, body: el("div", {}, crumbs, list), buttons: [["Use", () => picked, "primary"], ["Cancel", null]] });
  load("");
  return p;
}
