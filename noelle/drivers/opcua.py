"""OPC UA client driver (asyncua). Uses server-side subscriptions (no polling).

Addresses are NodeIds: ``ns=2;s=Channel1.Device1.Tag1``, ``ns=3;i=1001``,
``i=2258`` (server time). Device options: endpoint, username, password,
security ("Basic256Sha256,SignAndEncrypt,cert.der,key.pem"), publish_ms=250,
sampling_ms=0 (fastest), timeout_ms=4000, queue=1.
Direct: ``opcua:[opc.tcp://host:4840]ns=2;s=Tag``
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from ..engine import BAD_COMM, BAD_CONFIG, BAD_NOT_CONNECTED, GOOD, UNCERTAIN, Tag
from .base import Driver

log = logging.getLogger("noelle.opcua")


def _quality(status) -> int:
    try:
        v = status.value
    except AttributeError:
        v = int(status or 0)
    sev = v >> 30
    return GOOD if sev == 0 else UNCERTAIN if sev == 1 else BAD_COMM


def _py(v):
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ", timespec="milliseconds")
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (list, tuple)):
        return [_py(x) for x in v][:1000]
    return str(v)


class _Handler:
    def __init__(self, drv):
        self.drv = drv

    def datachange_notification(self, node, val, data):
        tags = self.drv.by_node.get(node.nodeid.to_string())
        if not tags:
            return
        dv = data.monitored_item.Value
        q = _quality(dv.StatusCode)
        ts = dv.SourceTimestamp or dv.ServerTimestamp
        t = ts.replace(tzinfo=dt.timezone.utc).timestamp() if ts else None
        v = _py(val)
        for tag in tags:
            self.drv.engine.update(tag, v, q, t)

    def status_change_notification(self, status):
        self.drv.lost.set()


class OpcUaDriver(Driver):
    kind = "opcua"

    def __init__(self, engine, name, cfg):
        cfg = dict(cfg or {})
        if "direct" in cfg:
            cfg["endpoint"] = cfg["direct"]
        super().__init__(engine, name, cfg)
        self.endpoint = cfg.get("endpoint", "opc.tcp://localhost:4840")
        self.client = None
        self.sub = None
        self.by_node: dict[str, list[Tag]] = {}
        self.handles: dict[str, int] = {}
        self.vtypes: dict[str, object] = {}
        self.pending_add: set[str] = set()
        self.pending_remove: set[str] = set()
        self.changed = asyncio.Event()
        self.lost = asyncio.Event()

    def parse(self, tag):
        return tag.parsed.address.strip()

    def add(self, tag):
        tag.item = self.parse(tag)
        self.tags.add(tag)
        lst = self.by_node.setdefault(tag.item, [])
        lst.append(tag)
        if len(lst) == 1:
            self.pending_remove.discard(tag.item)
            self.pending_add.add(tag.item)
            self.changed.set()
        if not self.connected:
            self.engine.update(tag, None, BAD_NOT_CONNECTED)

    def remove(self, tag):
        self.tags.discard(tag)
        lst = self.by_node.get(tag.item, [])
        if tag in lst:
            lst.remove(tag)
        if not lst:
            self.by_node.pop(tag.item, None)
            self.pending_add.discard(tag.item)
            self.pending_remove.add(tag.item)
            self.changed.set()

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _connect(self):
        try:
            from asyncua import Client
        except ImportError:
            raise RuntimeError("asyncua not installed (pip install asyncua)")
        c = Client(self.endpoint, timeout=float(self.cfg.get("timeout_ms", 4000)) / 1000)
        if self.cfg.get("username"):
            c.set_user(self.cfg["username"])
            c.set_password(self.cfg.get("password", ""))
        if self.cfg.get("security"):
            await c.set_security_string(self.cfg["security"])
        await c.connect()
        self.client = c
        self.sub = await c.create_subscription(int(self.cfg.get("publish_ms", 250)), _Handler(self))
        self.handles.clear()
        self.pending_add = set(self.by_node)
        self.pending_remove.clear()

    async def _sync(self):
        from asyncua import ua
        if self.pending_remove:
            ids = list(self.pending_remove)
            self.pending_remove.clear()
            hs = [self.handles.pop(i) for i in ids if i in self.handles]
            if hs:
                try:
                    await self.sub.unsubscribe(hs)
                except Exception as e:
                    log.debug("unsubscribe: %s", e)
        if self.pending_add:
            ids = list(self.pending_add)
            self.pending_add.clear()
            nodes, good_ids = [], []
            for i in ids:
                try:
                    nodes.append(self.client.get_node(i))
                    good_ids.append(i)
                except Exception as e:
                    for t in self.by_node.get(i, []):
                        t.error = f"bad NodeId: {e}"
                        self.engine.update(t, None, BAD_CONFIG)
            for k in range(0, len(nodes), 500):     # batch CreateMonitoredItems
                chunk, cids = nodes[k:k + 500], good_ids[k:k + 500]
                res = await self.sub.subscribe_data_change(
                    chunk, queuesize=int(self.cfg.get("queue", 1)),
                    sampling_interval=float(self.cfg.get("sampling_ms", 0)))
                if not isinstance(res, list):
                    res = [res]
                for nid, h in zip(cids, res):
                    if isinstance(h, int):
                        self.handles[nid] = h
                    else:
                        for t in self.by_node.get(nid, []):
                            t.error = str(h)
                            self.engine.update(t, None, BAD_CONFIG)
            _ = ua

    async def _run(self):
        backoff = 1.0
        while not self._stopping:
            if not self.by_node:
                self.changed.clear()
                await self.changed.wait()
                continue
            try:
                await self._connect()
                self.connected = True
                self.set_error("")
                backoff = 1.0
                self.lost.clear()
                while not self._stopping:
                    await self._sync()
                    self.changed.clear()
                    waiters = [asyncio.ensure_future(self.changed.wait()), asyncio.ensure_future(self.lost.wait())]
                    done, pend = await asyncio.wait(waiters, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
                    for w in pend:
                        w.cancel()
                    if self.lost.is_set():
                        raise ConnectionError("subscription lost")
                    if not done:  # keep-alive check
                        await self.client.get_node("i=2259").read_value()
                    if not self.by_node:
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.set_error(f"{type(e).__name__}: {e}")
                self.engine.set_quality_all(self, BAD_COMM)
            self.connected = False
            try:
                if self.client:
                    await self.client.disconnect()
            except Exception:
                pass
            self.client = None
            if self.by_node and not self._stopping:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass

    async def write(self, tag, value):
        from asyncua import ua
        if not self.client:
            raise ConnectionError("not connected")
        node = self.client.get_node(tag.item)
        vt = self.vtypes.get(tag.item)
        if vt is None:
            vt = await node.read_data_type_as_variant_type()
            self.vtypes[tag.item] = vt
        conv = _convert(value, vt, ua)
        await node.write_attribute(ua.AttributeIds.Value, ua.DataValue(ua.Variant(conv, vt)))
        self.stats["writes"] += 1

    async def browse(self, path=""):
        from asyncua import ua
        if not self.client:
            if not self.connected:
                await self._connect_for_browse()
        node = self.client.get_node(path or "i=85")
        refs = await node.get_references(refs=ua.ObjectIds.HierarchicalReferences,
                                         direction=ua.BrowseDirection.Forward)
        out = []
        for r in refs:
            nid = r.NodeId.to_string()
            is_var = r.NodeClass == ua.NodeClass.Variable
            out.append({"name": r.DisplayName.Text or r.BrowseName.Name, "path": nid,
                        "branch": r.NodeClass in (ua.NodeClass.Object, ua.NodeClass.Variable, ua.NodeClass.View),
                        "ref": f"opcua:{self._dev()}{nid}" if is_var else None})
        out.sort(key=lambda x: (x["ref"] is not None, x["name"].lower()))
        return out

    async def _connect_for_browse(self):
        await self._connect()
        self.connected = True

    def _dev(self):
        return f"[{self.endpoint}]" if self.name.startswith("opcua:[") else f"{self.name}/"

    async def stop(self):
        self._stopping = True
        self.changed.set()
        await super().stop()


def _convert(value, vt, ua):
    V = ua.VariantType
    if vt == V.Boolean:
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "off", "")
        return bool(value)
    if vt in (V.Float, V.Double):
        return float(value)
    if vt in (V.SByte, V.Byte, V.Int16, V.UInt16, V.Int32, V.UInt32, V.Int64, V.UInt64):
        return int(round(float(value)))
    if vt == V.String:
        return str(value)
    return value
