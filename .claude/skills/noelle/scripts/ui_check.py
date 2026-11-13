"""Open Noelle in headless Edge against a throwaway copy of a project, screenshot it, report console errors.

    .venv\\Scripts\\python .claude\\skills\\noelle\\scripts\\ui_check.py [options]

    --project DIR     project to copy (default projects\\Demo); the real folder is never written to
    --url PATH        page to open (default "/?mode=edit&display=displays/Main.nll")
                      other pages: "/?mode=run&display=...", "/?mode=historian", "/?render=1&display=..."
    --size WxH[,WxH]  viewport size(s); each size gets its own screenshot (default 1600x950)
    --eval JS         expression evaluated after the page settles, at every size (repeatable); result printed
    --wait SEC        seconds to wait after navigation (default 3)
    --out DIR         where screenshots go (default: the temp copy's folder, printed at the end)

Exit code 1 when the page logged errors or uncaught exceptions.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, ROOT)

from noelle.cdp import Browser  # noqa: E402
from noelle.importers.visual import _TempServer  # noqa: E402

SKIP = shutil.ignore_patterns("runtime", "logs", ".history", "__pycache__")


async def check(a, base_url, out_dir):
    sizes = [tuple(int(v) for v in s.lower().split("x")) for s in a.size.split(",")]
    w0, h0 = sizes[0]
    async with Browser(w0, h0) as b:
        await b.goto(base_url + a.url, a.wait)
        for w, h in sizes:
            if (w, h) != (b.width, b.height):
                b.width, b.height = w, h
                await b.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
                await asyncio.sleep(0.8)
            print(f"--- {w}x{h}")
            for js in a.eval:
                try:
                    v = await b.eval(f"JSON.stringify(({js}))")
                    print(f"  {js}\n    = {v if v is None else json.loads(v)}")
                except Exception as e:
                    print(f"  {js}\n    ! {e}")
            shot = os.path.join(out_dir, f"shot_{w}x{h}.png")
            await b.screenshot(shot)
            print(f"  screenshot: {shot}")
        bad = [c for c in b.console if c[0] in ("error", "exception")]
        warn = [c for c in b.console if c[0] == "warning"]
        for kind, msg in bad + warn:
            print(f"  console {kind}: {msg}")
        print("console: clean" if not bad else f"console: {len(bad)} error(s)")
        return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.path.join(ROOT, "projects", "Demo"))
    ap.add_argument("--url", default="/?mode=edit&display=displays/Main.nll")
    ap.add_argument("--size", default="1600x950")
    ap.add_argument("--eval", action="append", default=[])
    ap.add_argument("--wait", type=float, default=3.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="noelle-uicheck-")
    proj = os.path.join(tmp, os.path.basename(os.path.abspath(a.project)))
    shutil.copytree(a.project, proj, ignore=SKIP)
    out_dir = os.path.abspath(a.out) if a.out else tmp
    os.makedirs(out_dir, exist_ok=True)
    with _TempServer(proj) as srv:
        code = asyncio.run(check(a, srv.url, out_dir))
    if a.out:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("project copy + screenshots:", tmp)
    return code


if __name__ == "__main__":
    sys.exit(main())
