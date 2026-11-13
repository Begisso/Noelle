"""Visual identity testing: render Noelle displays with headless Edge/Chrome and
compare them pixel-by-pixel with reference renderings of the original software
(GraphWorX ExportBitmapCaptureOfView, or any screenshot you put in
runtime/identity/<name>.ref.png).

Metrics per display
    mismatch     share of pixels whose max channel difference > 40 (0 = identical)
    mae          mean absolute error, 0..1
    ssim         structural similarity on 4x downsampled grayscale (1 = identical)
    score        0..100 combined similarity (reported in the import report)
A diff image (<name>.diff.png) highlights differing pixels in red.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time

IDENT_DIR = ("runtime", "identity")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _TempServer:
    """Starts a Noelle server for the project in a background thread (CLI use)."""

    def __init__(self, root):
        self.root = root
        self.port = _free_port()
        self.ready = threading.Event()

    def __enter__(self):
        url = os.environ.get("GF_SERVER_URL")
        if url:
            self.url = url
            return self
        self.url = f"http://127.0.0.1:{self.port}"
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        self.ready.wait(30)
        return self

    def _run(self):
        from aiohttp import web
        from ..server import Server
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        srv = Server(self.root, "127.0.0.1", self.port)
        app = srv.app()
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", self.port).start())
        self.loop = loop
        self.ready.set()
        loop.run_forever()

    def __exit__(self, *a):
        if hasattr(self, "loop"):
            self.loop.call_soon_threadsafe(self.loop.stop)


async def render_many(base_url: str, items: list[tuple[str, str]]):
    """items: [(display_path, out_png)] -> list of (w, h)"""
    from ..cdp import Browser
    sizes = []
    async with Browser(2000, 1400) as b:
        for disp, out in items:
            await b.goto(f"{base_url}/?render=1&display={disp}", 0.5)
            for _ in range(100):
                ok = await b.eval("window.__rendered === true")
                if ok:
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)
            wh = await b.eval("JSON.stringify(window.__renderSize || [1280, 720])")
            import json
            w, h = json.loads(wh)
            await b.screenshot(out, {"x": 0, "y": 0, "width": w, "height": h})
            sizes.append((w, h))
    return sizes


def compare_images(ref_path: str, out_path: str, diff_path: str | None = None) -> dict:
    from PIL import Image, ImageChops
    a = Image.open(ref_path).convert("RGB")
    b = Image.open(out_path).convert("RGB")
    if b.size != a.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b)
    px = list(diff.getdata())
    n = len(px) or 1
    bad = sum(1 for p in px if max(p) > 40)
    mae = sum(sum(p) for p in px) / (n * 3 * 255)
    # SSIM on 4x downsampled grayscale (global windows of 8x8)
    ga = a.convert("L").resize((max(8, a.width // 4), max(8, a.height // 4)))
    gb = b.convert("L").resize(ga.size)
    ssim = _ssim(list(ga.getdata()), list(gb.getdata()), ga.width, ga.height)
    if diff_path:
        mask = diff.convert("L").point(lambda v: 255 if v > 40 else 0)
        base = a.convert("L").convert("RGB")
        red = Image.new("RGB", a.size, (255, 0, 0))
        Image.composite(red, Image.blend(base, Image.new("RGB", a.size, (0, 0, 0)), 0.5), mask).save(diff_path)
    mismatch = bad / n
    score = round(100 * (0.5 * (1 - mismatch) + 0.3 * max(0.0, ssim) + 0.2 * (1 - mae)), 1)
    return {"mismatch": round(mismatch, 4), "mae": round(mae, 4), "ssim": round(ssim, 4), "score": score, "size": list(a.size)}


def _ssim(x, y, w, h, win=8):
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    vals = []
    for by in range(0, h - win + 1, win):
        for bx in range(0, w - win + 1, win):
            xs = [x[(by + j) * w + bx + i] for j in range(win) for i in range(win)]
            ys = [y[(by + j) * w + bx + i] for j in range(win) for i in range(win)]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            vx = sum((v - mx) ** 2 for v in xs) / len(xs)
            vy = sum((v - my) ** 2 for v in ys) / len(ys)
            cxy = sum((xs[k] - mx) * (ys[k] - my) for k in range(len(xs))) / len(xs)
            vals.append(((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2)))
    return sum(vals) / len(vals) if vals else 1.0


def compare_displays(project, display_paths: list[str]) -> dict:
    """Render each display and compare with runtime/identity/<name>.ref.png."""
    d = os.path.join(project.root, *IDENT_DIR)
    os.makedirs(d, exist_ok=True)
    jobs = []
    for p in display_paths:
        name = os.path.splitext(os.path.basename(p))[0]
        if os.path.exists(os.path.join(d, f"{name}.ref.png")):
            jobs.append((p, name))
    if not jobs:
        return {}
    with _TempServer(project.root) as srv:
        items = [(p, os.path.join(d, f"{n}.render.png")) for p, n in jobs]
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(render_many(srv.url, items))
        finally:
            loop.close()
    out = {}
    for p, n in jobs:
        out[n] = compare_images(os.path.join(d, f"{n}.ref.png"), os.path.join(d, f"{n}.render.png"), os.path.join(d, f"{n}.diff.png"))
    return out


def cli_render(argv):
    import argparse
    from ..project import Project
    ap = argparse.ArgumentParser(prog="noelle render")
    ap.add_argument("project")
    ap.add_argument("display")
    ap.add_argument("out")
    ap.add_argument("--compare", default="", help="reference PNG to compare with")
    a = ap.parse_args(argv)
    proj = Project(a.project)
    with _TempServer(proj.root) as srv:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(render_many(srv.url, [(a.display, os.path.abspath(a.out))]))
        loop.close()
    print("rendered", a.out)
    if a.compare:
        print(compare_images(a.compare, a.out, os.path.splitext(a.out)[0] + ".diff.png"))
    return 0
