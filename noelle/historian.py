"""HTTP historian client/proxy (Flask "Historian OP" at e.g. http://192.168.70.100:5000).

Upstream API (reverse engineered from /charts):
    GET /get_groups                         -> {"groups": [...]}
    GET /get_subgroups?group=G              -> {"subgroups": [...]}
    GET /get_descriptions?group=G&subgroup=S-> {"descriptions": [...]}
    GET /search_tree?q=Q&limit=N            -> {"rows": [{description, group, subgroup}], "truncated"}
    GET /chart_data?descriptions=a,b&start=YYYY-MM-DD HH:MM:SS&end=...
                                            -> {desc: {timestamps: [...], values: [...], type: 0|1}}

Performance
-----------
* Data is cached in *hour chunks* (LRU); zoom/pan inside cached hours costs
  nothing upstream. Chunks that include "now" expire after ``live_ttl`` seconds.
* Missing consecutive hours (up to 7 days) for several pens are fetched in ONE
  upstream request, then split into chunks.
* Responses to the browser are downsampled per pixel column (first/min/max/last,
  "M4"), so a 1-year, 30M-sample trend still ships only ~4 points per pixel.
  Digital (type 0) series keep every transition.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import bisect
import email.utils
import logging
import time
from collections import OrderedDict

import math
import os
import struct
import zlib

import aiohttp

from .drivers.base import Driver
from .engine import BAD_COMM, BAD_CONFIG, GOOD

log = logging.getLogger("noelle.historian")
DAY = 3600.0   # cache chunk length (1 hour)
MAX_RUN = 168  # chunks per upstream request (7 days)


def parse_ts(s) -> float | None:
    if isinstance(s, (int, float)):
        return s / 1000.0 if s > 1e11 else float(s)
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d.timestamp()
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except Exception:
        return None


def fmt_ts(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def day_start(t: float) -> float:
    """start of the cache chunk containing t (hour aligned)."""
    return t - (t % DAY)


def m4(ts: list, vs: list, t0: float, t1: float, buckets: int):
    """Downsample keeping first/min/max/last per bucket. ts sorted ascending."""
    n = len(ts)
    if n <= buckets * 4:
        return ts, vs
    span = (t1 - t0) or 1.0
    out_t, out_v = [], []
    i = 0
    while i < n:
        b = int((ts[i] - t0) / span * buckets)
        j = i
        lo_i = hi_i = i
        lo = hi = vs[i]
        while j + 1 < n and int((ts[j + 1] - t0) / span * buckets) == b:
            j += 1
            v = vs[j]
            if v is None:
                continue
            if lo is None or v < lo:
                lo, lo_i = v, j
            if hi is None or v > hi:
                hi, hi_i = v, j
        for k in sorted({i, lo_i, hi_i, j}):
            out_t.append(ts[k])
            out_v.append(vs[k])
        i = j + 1
    return out_t, out_v


def transitions(ts, vs):
    if len(ts) < 3:
        return ts, vs
    ot, ov = [ts[0]], [vs[0]]
    for k in range(1, len(ts)):
        if vs[k] != ov[-1]:
            if ot[-1] != ts[k - 1]:
                ot.append(ts[k - 1])
                ov.append(vs[k - 1])
            ot.append(ts[k])
            ov.append(vs[k])
    if ot[-1] != ts[-1]:
        ot.append(ts[-1])
        ov.append(vs[-1])
    return ot, ov


def _typ(t) -> int:
    """0 = discrete/stepped series, 1 = analog (upstream sometimes sends '')."""
    try:
        return int(t)
    except (TypeError, ValueError):
        return 1


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, bool):
        return int(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class DiskCache:
    """Persistent cache of *closed* chunks (older than now) in SQLite. Values are
    stored as packed float64 arrays (zlib) - a pen-day is ~20-60 KB on disk."""

    def __init__(self, path: str):
        import sqlite3
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS chunk (descr TEXT, t0 REAL, typ INT, n INT, data BLOB, PRIMARY KEY(descr, t0))")
        self.pending = []

    def get(self, d, t0):
        r = self.db.execute("SELECT typ, n, data FROM chunk WHERE descr=? AND t0=?", (d, t0)).fetchone()
        if not r:
            return None
        typ, n, blob = r
        arr = struct.unpack(f"<{2 * n}d", zlib.decompress(blob)) if n else ()
        vs = [None if math.isnan(v) else v for v in arr[n:]]
        return list(arr[:n]), vs, typ

    def put(self, d, t0, ts, vs, typ):
        n = len(ts)
        blob = zlib.compress(struct.pack(f"<{2 * n}d", *ts, *[float("nan") if v is None else float(v) for v in vs]), 6)
        self.pending.append((d, t0, typ, n, blob))
        if len(self.pending) > 200:
            self.flush()

    def flush(self):
        if self.pending:
            self.db.executemany("INSERT OR REPLACE INTO chunk VALUES (?,?,?,?,?)", self.pending)
            self.db.commit()
            self.pending.clear()


class Historian:
    def __init__(self, url: str, live_ttl: float = 30.0, max_chunks: int = 20000, timeout: float = 300.0,
                 cache_path: str | None = None, concurrency: int = 4):
        self.url = url.rstrip("/")
        self.live_ttl = live_ttl
        self.max_chunks = max_chunks
        self.timeout = timeout
        self.cache: OrderedDict = OrderedDict()   # (desc, chunk_t0) -> (fetched_at, ts[], vs[], type)
        self.inflight: dict = {}                  # (desc, chunk_t0) -> Future (per chunk dedupe)
        self.sem = asyncio.Semaphore(concurrency)  # parallel upstream requests (one pen each)
        self.session: aiohttp.ClientSession | None = None
        self.disk = None
        if cache_path:
            try:
                self.disk = DiskCache(cache_path)
            except Exception as e:  # pragma: no cover
                log.warning("historian disk cache disabled: %s", e)
        self.stats = {"upstream": 0, "cache_hits": 0, "disk_hits": 0, "errors": 0, "last_error": "", "last_upstream_s": 0}

    async def _sess(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self.session

    async def close(self):
        if self.disk:
            self.disk.flush()
        if self.session:
            await self.session.close()

    async def _get(self, path, params=None):
        s = await self._sess()
        async with self.sem:
            self.stats["upstream"] += 1
            t = time.perf_counter()
            try:
                async with s.get(self.url + path, params=params) as r:
                    r.raise_for_status()
                    return await r.json(content_type=None)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{path}: {type(e).__name__} {e}"
                raise
            finally:
                self.stats["last_upstream_s"] = round(time.perf_counter() - t, 2)

    # ------------------------------------------------------------------ tree
    async def groups(self):
        return (await self._get("/get_groups")).get("groups", [])

    async def subgroups(self, group):
        return (await self._get("/get_subgroups", {"group": group})).get("subgroups", [])

    async def descriptions(self, group, subgroup):
        return (await self._get("/get_descriptions", {"group": group, "subgroup": subgroup})).get("descriptions", [])

    async def search(self, q, limit=500):
        return await self._get("/search_tree", {"q": q, "limit": str(limit)})

    # ------------------------------------------------------------------ data
    async def _fetch_run(self, d: str, chunks: list[float]):
        """Fetch consecutive chunks of ONE pen in one upstream request, split into chunks."""
        start = chunks[0]
        end = min(chunks[-1] + DAY - 0.001, time.time() + 60)
        data = await self._get("/chart_data", {"descriptions": d, "start": fmt_ts(start), "end": fmt_ts(end)})
        now = time.time()
        entry = data.get(d) or {}
        pairs = []
        for t, v in zip(entry.get("timestamps") or [], entry.get("values") or []):
            tt = parse_ts(t)
            if tt is not None:
                pairs.append((tt, _num(v)))
        pairs.sort(key=lambda p: p[0])
        all_t = [p[0] for p in pairs]
        all_v = [p[1] for p in pairs]
        typ = _typ(entry.get("type"))
        for c in chunks:
            i, j = bisect.bisect_left(all_t, c), bisect.bisect_left(all_t, c + DAY)
            self.cache[(d, c)] = (now, all_t[i:j], all_v[i:j], typ)
            self.cache.move_to_end((d, c))
            if self.disk and c + DAY < now - 300:          # closed chunk -> persist
                self.disk.put(d, c, all_t[i:j], all_v[i:j], typ)
        if self.disk:
            self.disk.flush()
        while len(self.cache) > self.max_chunks:
            self.cache.popitem(last=False)

    def _cached(self, d, c):
        e = self.cache.get((d, c))
        if e:
            fetched = e[0]
            if c + DAY > fetched and time.time() - fetched > self.live_ttl:
                return None   # chunk covers "now" -> stale after live_ttl
            self.cache.move_to_end((d, c))
            return e
        if self.disk and c + DAY < time.time() - 300:
            r = self.disk.get(d, c)
            if r:
                self.stats["disk_hits"] += 1
                e = (time.time(), r[0], r[1], r[2])
                self.cache[(d, c)] = e
                return e
        return None

    async def data(self, descs: list[str], t0: float, t1: float, px: int = 1500, raw: bool = False):
        chunks = []
        c = day_start(t0)
        while c <= t1:
            chunks.append(c)
            c += DAY
        waits = []
        for d in descs:
            run: list[float] = []
            runs: list[list[float]] = []
            for c in chunks:
                if self._cached(d, c) is not None:
                    self.stats["cache_hits"] += 1
                    continue
                f = self.inflight.get((d, c))
                if f is not None:              # another request already fetches it
                    waits.append(f)
                    continue
                if run and run[-1] + DAY == c and len(run) < MAX_RUN:
                    run.append(c)
                else:
                    run = [c]
                    runs.append(run)
            for r in runs:                     # one upstream request per pen-run, run in parallel
                fut = asyncio.ensure_future(self._fetch_run(d, r))
                for c in r:
                    self.inflight[(d, c)] = fut
                fut.add_done_callback(lambda _f, d=d, r=r: [self.inflight.pop((d, c), None) for c in r])
                waits.append(fut)
        if waits:
            res = await asyncio.gather(*set(waits), return_exceptions=True)
            errs = [e for e in res if isinstance(e, Exception)]
            if errs and len(errs) == len(res):
                raise errs[0]
        out = {}
        for x in descs:
            ts, vs, typ = [], [], 1
            for c in chunks:
                e = self.cache.get((x, c))
                if not e:
                    continue
                _, cts, cvs, typ = e
                a = bisect.bisect_left(cts, t0)
                b = bisect.bisect_right(cts, t1)
                ts.extend(cts[a:b])
                vs.extend(cvs[a:b])
            n_raw = len(ts)
            if not raw:
                if typ == 0:
                    ts, vs = transitions(ts, vs)
                    if len(ts) > px * 4:
                        ts, vs = m4(ts, vs, t0, t1, max(50, px))
                else:
                    ts, vs = m4(ts, vs, t0, t1, max(50, px))
            out[x] = {"t": [round(t * 1000) for t in ts], "v": vs, "type": typ, "raw_count": n_raw}
        return out

    async def last_values(self, descs: list[str], window_s: float = 900):
        now = time.time()
        params = {"descriptions": ",".join(descs), "start": fmt_ts(now - window_s), "end": fmt_ts(now + 60)}
        data = await self._get("/chart_data", params)
        out = {}
        for d in descs:
            e = data.get(d) or {}
            ts, vs = e.get("timestamps") or [], e.get("values") or []
            if ts:
                best = max(range(len(ts)), key=lambda i: parse_ts(ts[i]) or 0)
                out[d] = (_num(vs[best]), parse_ts(ts[best]))
        return out

    def make_driver(self, engine, name, cfg):
        return HistDriver(engine, name, cfg, self)

    def status(self):
        return {"url": self.url, "cached_chunks": len(self.cache), "inflight": len(self.inflight), **self.stats}


class HistDriver(Driver):
    """``hist:<description>`` - last value of a historian series (polled)."""
    kind = "hist"
    writable = False

    def __init__(self, engine, name, cfg, hist: Historian):
        super().__init__(engine, name, cfg)
        self.hist = hist
        self.rate = 10.0
        self.kick = asyncio.Event()

    def add(self, tag):
        super().add(tag)
        self.kick.set()

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self):
        while not self._stopping:
            if not self.tags:
                self.kick.clear()
                await self.kick.wait()
            tags = list(self.tags)
            descs = sorted({t.item for t in tags})
            try:
                vals = {}
                for k in range(0, len(descs), 50):
                    vals.update(await self.hist.last_values(descs[k:k + 50]))
                self.connected = True
                self.set_error("")
                for t in tags:
                    if t.item in vals:
                        v, ts = vals[t.item]
                        self.engine.update(t, v, GOOD, ts)
                    else:
                        t.error = "no data in the last 15 minutes"
                        self.engine.update(t, t.value, BAD_CONFIG if t.value is None else 64)
                self.stats["reads"] += 1
            except Exception as e:
                self.connected = False
                self.set_error(str(e))
                self.engine.set_quality_all(self, BAD_COMM)
            self.kick.clear()
            try:
                await asyncio.wait_for(self.kick.wait(), self.rate)
            except asyncio.TimeoutError:
                pass
