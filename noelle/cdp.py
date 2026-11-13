"""Minimal Chrome DevTools Protocol client (headless Edge/Chrome) used for
rendering displays to PNG (visual identity tests) and browser smoke tests."""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import os
import shutil
import socket
import subprocess
import tempfile

import aiohttp


def find_browser() -> str | None:
    cands = []
    for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(k, "")
        cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                  os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Browser:
    def __init__(self, width=1600, height=950):
        self.width, self.height = width, height
        self.proc = None
        self.ws = None
        self.session = None
        self.ids = itertools.count(1)
        self.pending = {}
        self.events = []
        self.console = []

    async def __aenter__(self):
        exe = find_browser()
        if not exe:
            raise RuntimeError("Edge/Chrome not found")
        port = _free_port()
        self.profile = tempfile.mkdtemp(prefix="gf-cdp-")
        self.proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
                                      f"--window-size={self.width},{self.height}", "--hide-scrollbars", "--mute-audio",
                                      "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.session = aiohttp.ClientSession()
        for _ in range(100):
            try:
                async with self.session.get(f"http://127.0.0.1:{port}/json/list") as r:
                    tabs = await r.json()
                    page = next(t for t in tabs if t.get("type") == "page")
                    break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("browser did not start")
        self.ws = await self.session.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=256 * 1024 * 1024)
        self.rx = asyncio.get_running_loop().create_task(self._rx())
        await self.send("Runtime.enable")
        await self.send("Page.enable")
        await self.send("Log.enable")
        await self.send("Emulation.setDeviceMetricsOverride", {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False})
        return self

    async def __aexit__(self, *a):
        try:
            self.rx.cancel()
            await self.ws.close()
            await self.session.close()
        finally:
            if self.proc:
                self.proc.kill()
            shutil.rmtree(self.profile, ignore_errors=True)

    async def _rx(self):
        async for m in self.ws:
            d = json.loads(m.data)
            if "id" in d:
                f = self.pending.pop(d["id"], None)
                if f and not f.done():
                    f.set_result(d)
            else:
                self.events.append(d)
                meth = d.get("method")
                if meth == "Runtime.consoleAPICalled":
                    args = " ".join(str(a.get("value", a.get("description", ""))) for a in d["params"]["args"])
                    self.console.append((d["params"]["type"], args))
                elif meth == "Runtime.exceptionThrown":
                    ex = d["params"]["exceptionDetails"]
                    self.console.append(("exception", ex.get("exception", {}).get("description") or ex.get("text")))
                elif meth == "Log.entryAdded":
                    e = d["params"]["entry"]
                    if e.get("level") in ("error", "warning"):
                        self.console.append((e["level"], f"{e.get('text')} {e.get('url', '')}"))

    async def send(self, method, params=None, timeout=60):
        i = next(self.ids)
        f = asyncio.get_running_loop().create_future()
        self.pending[i] = f
        await self.ws.send_str(json.dumps({"id": i, "method": method, "params": params or {}}))
        r = await asyncio.wait_for(f, timeout)
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r.get("result", {})

    async def goto(self, url, wait=2.0):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait)

    async def eval(self, expr, await_promise=True):
        r = await self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": await_promise, "returnByValue": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")

    async def screenshot(self, path, clip=None):
        p = {"format": "png"}
        if clip:
            p["clip"] = {**clip, "scale": 1}
        r = await self.send("Page.captureScreenshot", p)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    async def click(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
