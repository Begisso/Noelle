// WebSocket connection + reference counted subscriptions.
//
// Performance: every distinct reference is subscribed ONCE per browser tab no
// matter how many objects use it; listeners are plain callbacks invoked from a
// single requestAnimationFrame batch, so a burst of updates causes at most one
// DOM pass per frame.

export const api = {
  async get(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText); return r.headers.get("content-type")?.includes("json") ? r.json() : r.text(); },
  async text(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(r.statusText); return r.text(); },
  async send(method, url, body, isText = false) {
    const r = await fetch(url, { method, headers: { "Content-Type": isText ? "text/plain;charset=utf-8" : "application/json" }, body: isText ? body : JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  },
  put(url, body, isText) { return this.send("PUT", url, body, isText); },
  post(url, body, isText) { return this.send("POST", url, body, isText); },
};

export const Q = { GOOD: 192, UNCERTAIN: 64, BAD: 0, CONFIG: 4, NOT_CONNECTED: 8, COMM: 24 };

class Point {
  constructor(ref, cid) {
    this.ref = ref; this.cid = cid;
    this.value = undefined; this.q = Q.NOT_CONNECTED; this.ts = 0;
    this.listeners = new Set(); this.meta = null; this.hist = false;
  }
}

export class Connection extends EventTarget {
  constructor() {
    super();
    this.points = new Map();    // ref -> Point
    this.byCid = new Map();     // cid -> Point
    this.nextCid = 1;
    this.rid = 1;
    this.pending = new Map();   // rid -> {resolve, reject}
    this.dirty = new Set();
    this.frame = 0;
    this.toSub = new Map();
    this.toUnsub = new Set();
    this.user = "?"; this.role = "viewer"; this.aliases = {};
    this.connected = false;
    this.stats = { msgs: 0, values: 0, lastRtt: 0 };
    this.connect();
  }

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      // resubscribe everything after reconnect
      const items = [...this.points.values()].map((p) => [p.cid, p.ref, p.hist ? "h" : ""]);
      if (items.length) this._send({ op: "sub", items });
      this.dispatchEvent(new Event("open"));
    };
    ws.onclose = () => {
      this.connected = false;
      for (const p of this.points.values()) { p.q = Q.NOT_CONNECTED; this._mark(p); }
      for (const [, w] of this.pending) w.reject(new Error("disconnected"));
      this.pending.clear();
      this.dispatchEvent(new Event("close"));
      setTimeout(() => this.connect(), 1500);
    };
    ws.onmessage = (e) => this._onMessage(JSON.parse(e.data));
  }

  _send(obj) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj)); }

  _onMessage(m) {
    this.stats.msgs++;
    switch (m.op) {
      case "v":
        for (const [cid, v, q, ts] of m.d) {
          const p = this.byCid.get(cid);
          if (!p) continue;
          p.value = v; p.q = q; p.ts = ts;
          this._mark(p);
        }
        this.stats.values += m.d.length;
        break;
      case "m":
        for (const [cid, meta] of m.d) { const p = this.byCid.get(cid); if (p) { p.meta = meta; this._mark(p); } }
        break;
      case "res": {
        const w = this.pending.get(m.rid);
        if (w) { this.pending.delete(m.rid); m.ok ? w.resolve(m.result) : w.reject(new Error(m.error)); }
        break;
      }
      case "welcome":
        this.user = m.user; this.role = m.role; this.aliases = m.aliases || {}; this.security = m.security;
        this.dispatchEvent(new CustomEvent("welcome", { detail: m }));
        break;
      default:
        this.dispatchEvent(new CustomEvent(m.op, { detail: m }));
    }
  }

  _mark(p) {
    this.dirty.add(p);
    if (!this.frame) this.frame = requestAnimationFrame(() => this._deliver());
  }

  _deliver() {
    this.frame = 0;
    const list = [...this.dirty];
    this.dirty.clear();
    for (const p of list) for (const fn of p.listeners) { try { fn(p); } catch (e) { console.error(e); } }
  }

  // ------------------------------------------------------------ subscriptions
  subscribe(ref, fn, { history = false } = {}) {
    let p = this.points.get(ref);
    if (!p) {
      p = new Point(ref, this.nextCid++);
      this.points.set(ref, p);
      this.byCid.set(p.cid, p);
      this.toUnsub.delete(p.cid);
      this.toSub.set(p.cid, p);
      this._flushSubs();
    }
    if (history && !p.hist) { p.hist = true; this.toSub.set(p.cid, p); this._flushSubs(); }
    p.listeners.add(fn);
    if (p.value !== undefined || p.q !== Q.NOT_CONNECTED) queueMicrotask(() => p.listeners.has(fn) && fn(p));
    return p;
  }

  unsubscribe(ref, fn) {
    const p = this.points.get(ref);
    if (!p) return;
    p.listeners.delete(fn);
    if (p.listeners.size === 0) {
      // keep for a moment: switching displays often re-subscribes the same tags
      setTimeout(() => {
        if (p.listeners.size === 0 && this.points.get(ref) === p) {
          this.points.delete(ref); this.byCid.delete(p.cid);
          this.toSub.delete(p.cid); this.toUnsub.add(p.cid); this._flushSubs();
        }
      }, 3000);
    }
  }

  _flushSubs() {
    if (this._subTimer) return;
    this._subTimer = setTimeout(() => {
      this._subTimer = 0;
      if (this.toSub.size) {
        this._send({ op: "sub", items: [...this.toSub.values()].map((p) => [p.cid, p.ref, p.hist ? "h" : ""]) });
        this.toSub.clear();
      }
      if (this.toUnsub.size) { this._send({ op: "unsub", ids: [...this.toUnsub] }); this.toUnsub.clear(); }
    }, 0);
  }

  // ------------------------------------------------------------ requests
  request(msg, timeout = 30000) {
    return new Promise((resolve, reject) => {
      if (!this.connected) return reject(new Error("not connected to server"));
      const rid = this.rid++;
      this.pending.set(rid, { resolve, reject });
      this._send({ ...msg, rid });
      setTimeout(() => { if (this.pending.has(rid)) { this.pending.delete(rid); reject(new Error("timeout")); } }, timeout);
    });
  }
  write(ref, value) { return this.request({ op: "write", ref, value }); }
  rpc(fn, args) { return this.request({ op: "rpc", fn, args }); }
  value(ref) { const p = this.points.get(ref); return p ? p.value : undefined; }
}
