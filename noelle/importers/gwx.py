"""ICONICS GraphWorX32 (.gdf) importer.

1. ``gwx_export.ps1`` (32-bit PowerShell) drives a private GraphWorX32 instance via
   COM, dumps all objects/dynamics to JSON and renders reference bitmaps.
2. This module converts the JSON into a Noelle display (.nll).

Conversion (default "native")
-----------------------------
Every GraphWorX object becomes a native, editable Noelle object. Where the automation
API hides geometry, the exporter renders that ONE object alone on a transparent
background and the importer recovers it:
* polylines / polygons  -> vectorized (contour or skeleton) into real points
* bitmaps, metafiles, OLE controls -> image objects (they ARE raster content in the .gdf)
* pattern / bitmap brushes -> texture fill of the converted shape
GraphWorX's full rendering is only used as the reference of the visual identity test
(runtime/identity/*.ref.png) and is never part of the imported display.
"hybrid" (CLI only) additionally bakes static artwork into a background image.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from ..project import Project

HERE = os.path.dirname(os.path.abspath(__file__))
PICK = ["LoadDisplay", "DragDropLoad", "PopupWindow", "DownloadValue", "ToggleValue", "LaunchApp", "Close", "RunScript",
        "CustomCommand", "EmbeddedWindow", "DisplayBack", "DisplayForward", "SetAliases", "AliasesDialog", "LayerHideOn",
        "LayerHideOff", "LayerHideToggle", "PopupUserMenu", "SwitchLanguage", "SelectThema", "LaunchWebPage"]
LINE_NONE = 5


def powershell32():
    win = os.environ.get("windir", r"C:\Windows")
    p = os.path.join(win, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.exists(p) else "powershell.exe"


def color(c, default="none"):
    """GraphWorX COLORREF (0x02BBGGRR / 0x00BBGGRR) -> #rrggbb"""
    if c is None:
        return default
    c = int(c) & 0xFFFFFF
    return f"#{c & 0xFF:02x}{(c >> 8) & 0xFF:02x}{(c >> 16) & 0xFF:02x}"


class TagMapper:
    """GraphWorX data source -> Noelle reference."""

    def __init__(self, rules: str = ""):
        self.rules = []
        for part in (rules or "").split(";"):
            if "=" in part:
                a, b = part.split("=", 1)
                self.rules.append((a.strip(), b.strip()))

    def ref(self, src: str) -> str:
        s = (src or "").strip()
        if not s:
            return ""
        s = re.sub(r"<<([^<>]+)>>", r"<#\1#>", s)     # ICONICS global alias -> Noelle alias
        for a, b in self.rules:
            if s.startswith(a):
                rest = s[len(a):].lstrip("\\")
                return b + rest
        m = re.fullmatch(r"~~(.+?)~~", s)
        if m:                                   # GraphWorX local variable
            return f"mem:{m.group(1)}"
        if s.startswith("@") and s.endswith("@"):
            return f"mem:{s.strip('@')}"
        if "\\" in s and not s.startswith("<#"):
            server, item = s.split("\\", 1)     # OPC DA: ProgID\ItemID
            if server.lower().startswith("iconics.simulator"):
                return f"opcda:[{server}]{item}"
            return f"opcda:[{server}]{item}"
        return s                                # tag database name / alias

    def expr(self, src: str) -> str:
        """data source (tag or ICONICS expression 'x=...{{tag}}...') -> Noelle expression."""
        s = (src or "").strip()
        if not s:
            return ""
        s = re.sub(r"<<([^<>]+)>>", r"<#\1#>", s)
        if s.lower().startswith("x="):
            body = s[2:]
            body = re.sub(r"\{\{(.+?)\}\}", lambda m: "{{" + self.ref(m.group(1)) + "}}", body)
            body = body.replace("<>", "!=").replace(" AND ", " && ").replace(" OR ", " || ").replace(" NOT ", " !")
            return body
        return "{{" + self.ref(s) + "}}"


class Converter:
    def __init__(self, dump: dict, mapper: TagMapper, mode: str, img_rel: str | None, folder: str):
        self.d = dump
        self.map = mapper
        self.mode = mode
        self.bg_rel = img_rel
        self.folder = folder
        self.warn: list[str] = []
        self.n = 0
        self.func = {"bindings": 0, "converted": 0, "approximated": 0, "unsupported": 0, "items": []}

    def uid(self, p="o"):
        self.n += 1
        return f"{p}{self.n}"

    def note(self, kind, src, what):
        self.func[kind] += 1
        self.func["items"].append({"status": kind, "source": src, "as": what})

    # ------------------------------------------------------------------ objects
    def convert_display(self, name):
        W, H = int(self.d.get("width") or 1024), int(self.d.get("height") or 768)
        out = []
        if self.mode == "hybrid" and self.bg_rel:
            out.append({"id": "gwx_background", "type": "image", "x": 0, "y": 0, "w": W, "h": H, "src": self.bg_rel,
                        "fit": "stretch", "locked": True, "tooltip": "static artwork baked from GraphWorX"})
        for o in self.d.get("objects", []):
            if self.mode == "hybrid" and self.bg_rel:
                out += self.hybrid(o)
            else:
                c = self.obj(o)
                if c:
                    out.append(c)
        if self.mode != "hybrid":
            out = self.fix_backgrounds(out)
        return {"format": "noelle-display", "version": 1, "name": name, "size": [W, H],
                "background": color(self.d.get("background"), "#000000"), "aliases": {},
                "source": {"importer": "graphworx32", "file": self.d.get("file"), "mode": self.mode},
                "objects": out}

    @staticmethod
    def visual_dyn(o):
        return any((d.get("_type") or "") != "GwxPick" for d in o.get("_dyn", []))

    def hybrid(self, o):
        """Static artwork is in the background PNG. Objects with visual dynamics become
        native objects; click-only objects become invisible hotspots with the actions."""
        if self.visual_dyn(o):
            c = self.obj(o)
            return [c] if c else []
        out = []
        picks = [d for d in o.get("_dyn", []) if (d.get("_type") or "") == "GwxPick"]
        if picks:
            l, t, w, h = (float(v) for v in o.get("_dims", [0, 0, 0, 0]))
            hs = {"id": self.uid("hot"), "type": "rect", "x": round(l, 2), "y": round(t, 2), "w": round(max(w, 2), 2), "h": round(max(h, 2), 2),
                  "fill": "rgba(0,0,0,0)", "stroke": "none", "sw": 0}
            if o.get("UserDescription"):
                hs["tooltip"] = o["UserDescription"]
            acts = []
            for d in picks:
                if d.get("dataSource"):
                    self.func["bindings"] += 1
                acts += self.pick(d, d.get("dataSource") or "")
            if acts:
                hs["actions"] = acts
                out.append(hs)
        for c in o.get("_children", []):
            out += self.hybrid(c)
        return out

    def obj(self, o):
        t = (o.get("_type") or "").replace("Gwx", "")
        l, top, w, h = (float(x) for x in o.get("_dims", [0, 0, 0, 0]))
        x, y, w, h = round(l, 2), round(top, 2), round(max(w, 1), 2), round(max(h, 1), 2)
        fill = color(o.get("fillColor")) if o.get("isFilled") else "none"
        stroke = "none" if o.get("lineStyle") == LINE_NONE else color(o.get("lineColor"))
        sw = max(1, int(o.get("lineWidth") or 0)) if stroke != "none" else 0
        edge = int(o.get("edgeStyle") or 0)
        if edge and stroke == "none" and t in ("Text", "Rectangle", "Ellipse"):
            # GraphWorX 3D edges (raised / etched / bump / sunken) draw a frame even without a line
            stroke, sw = ("#808080", 1) if edge in (4, 5, 6, 10) else ("#a0a0a0", 1)
        dash = {1: "6,3", 2: "2,2", 3: "6,2,2,2", 4: "6,2,2,2,2,2"}.get(o.get("lineStyle"))
        base = {"id": self.uid(t[:1].lower() or "o"), "x": x, "y": y, "w": w, "h": h}
        if o.get("objectName"):
            base["name"] = o["objectName"]
        if o.get("Angle"):
            ang = float(o["Angle"])
            base["rot"] = -ang
            # GraphWorX reports the axis aligned box of the ROTATED shape: recover the unrotated size
            import math
            c, sn = abs(math.cos(math.radians(ang))), abs(math.sin(math.radians(ang)))
            den = c * c - sn * sn
            if abs(den) > 0.2:
                uw, uh = (w * c - h * sn) / den, (h * c - w * sn) / den
                if uw > 0.5 and uh > 0.5:
                    cx, cy = x + w / 2, y + h / 2
                    base.update(x=round(cx - uw / 2, 2), y=round(cy - uh / 2, 2), w=round(uw, 2), h=round(uh, 2))
                    x, y, w, h = base["x"], base["y"], base["w"], base["h"]
        if o.get("UserDescription"):
            base["tooltip"] = o["UserDescription"]
        if o.get("Visible") is False:
            base["invisible"] = True          # hidden in GraphWorX: not shown at runtime (faded in the editor)
        if o.get("hasShadow"):
            base["shadow"] = True
        if dash:
            base["dash"] = dash
        if t == "Rectangle":
            r = {**base, "type": "rect", "fill": fill, "stroke": stroke, "sw": sw}
            if o.get("rounded"):
                r["rx"] = max(int(o.get("RoundingX") or 0), int(o.get("RoundingY") or 0)) / 2 or 6
        elif t == "Ellipse":
            r = {**base, "type": "ellipse", "fill": fill, "stroke": stroke, "sw": sw}
        elif t == "Arc":
            a0, a1 = float(o.get("startAngle") or 0), float(o.get("endAngle") or 360)
            r = {**base, "type": "arc", "fill": fill, "stroke": stroke, "sw": sw, "start": round(90 - a1, 2), "end": round(90 - a0, 2),
                 "arcType": ["arc", "pie", "chord"][int(o.get("arcType") or 0) % 3]}
        elif t in ("Text", "Button"):
            f = o.get("Font") or {}
            pt = float(f.get("Size") or 10)
            text = o.get("text", "")
            lines = max(1, str(text).count("\n") + 1)
            # GraphWorX sizes text boxes to the font: derive the pixel size from the box height,
            # never larger than the nominal point size
            fs = round(min(pt * 96 / 72, (h / lines) / 1.15), 1)
            stretch = bool(o.get("stretchText"))     # GraphWorX scales text to fill the box
            r = {**base, "type": "button" if t == "Button" else "text", "text": text, "fs": fs, "font": f.get("Name") or "Arial",
                 "fw": bool(f.get("Bold")), "italic": bool(f.get("Italic")), "color": color(o.get("lineColor"), "#000000"),
                 "align": ["left", "center", "right"][int(o.get("alignment") or 0) % 3], "valign": "middle",
                 "fill": fill, "stroke": stroke if edge else "none"}
            if stretch and t == "Text":
                r["stretch"] = True
            if t == "Button":
                r.update(fill=color(o.get("fillColor"), "#c0c0c0"), color=color(o.get("lineColor"), "#000000"), style="3d", rx=2)
        elif t == "Symbol":
            kids = [self.obj(c) for c in o.get("_children", [])]
            r = {"id": base["id"], "type": "group", "children": [k for k in kids if k]}
            if "name" in base:
                r["name"] = base["name"]
            if "tooltip" in base:
                r["tooltip"] = base["tooltip"]
        elif t == "Line":
            lw = max(1, int(o.get("lineWidth") or 1))
            vec = self.vector(o)
            if vec:
                pts = vec["points"]
                filled = bool(o.get("isFilled")) and len(pts) > 2
                kind = "polygon" if filled or (vec["closed"] and len(pts) > 2) else ("line" if len(pts) == 2 else "polyline")
                r = {"id": base["id"], "type": kind, "points": pts, "stroke": stroke if stroke != "none" else "none",
                     "sw": lw if stroke != "none" else 0, "fill": fill if filled else "none"}
                if dash:
                    r["dash"] = dash
                if o.get("_raster_kind") == "shape" and filled:
                    tex = self.raster_texture(o)
                    if tex:
                        r["fillImage"], r["fillImageBox"] = tex
                self.func["shapes_vectorized"] = self.func.get("shapes_vectorized", 0) + 1
            elif o.get("_points") and len(o["_points"]) >= 2:
                pts = [[round(p[0], 1), round(p[1], 1)] for p in o["_points"]]
                filled = bool(o.get("isFilled")) and len(pts) > 2
                r = {"id": base["id"], "type": "polygon" if filled else ("line" if len(pts) == 2 else "polyline"), "points": pts,
                     "stroke": stroke if stroke != "none" else "none", "sw": lw if stroke != "none" else 0, "fill": fill if filled else "none"}
                if (o.get("_trace") or {}).get("trace", "").startswith("hull") and not filled:
                    self.warn.append(f"open polyline at ({x:.0f},{y:.0f}) {w:.0f}x{h:.0f} traced as convex outline - check it")
            elif w <= 2 or h <= 2:     # horizontal / vertical: bounding box is exact
                r = {"id": base["id"], "type": "line", "points": [[x, y], [x + w, y + h]], "stroke": color(o.get("lineColor")), "sw": lw}
            else:
                r = {"id": base["id"], "type": "line", "points": [[x, y], [x + w, y + h]], "stroke": color(o.get("lineColor")), "sw": lw}
                self.warn.append(f"polyline at ({x:.0f},{y:.0f}) {w:.0f}x{h:.0f}: vertices could not be recovered - approximated by its diagonal")
            if "name" in base:
                r["name"] = base["name"]
        elif t in ("Bitmap", "Metafile", "OleObject"):
            img = self.raster_file(o)
            crop = img[0] if img else self.crop(x, y, w, h)
            if img:
                rx, ry, rw, rh = img[1]
                r = {**base, "type": "image", "src": crop, "fit": "stretch", "x": rx, "y": ry, "w": rw, "h": rh}
            elif crop:
                r = {**base, "type": "image", "src": crop, "fit": "stretch"}
            else:
                r = {**base, "type": "rect", "fill": "none", "stroke": "#ff00ff", "dash": "4,3"}
                self.warn.append(f"{t} at ({x:.0f},{y:.0f}) could not be converted (no bitmap capture)")
        else:
            r = {**base, "type": "rect", "fill": "none", "stroke": "#ff00ff", "dash": "4,3"}
            self.warn.append(f"unknown GraphWorX object type {t}")
        self.dynamics(o, r)
        if r.get("type") in ("rect", "ellipse", "arc") and o.get("_raster_kind") == "texture":
            tex = self.raster_texture(o)
            if tex:
                r["fillImage"], r["fillImageBox"] = tex
        return r

    # ------------------------------------------------------------------ stacking check against the reference
    def fix_backgrounds(self, out):
        """A big filled shape (panel / frame background) that ended up ABOVE smaller objects lying
        inside it is moved below them when those objects are visible in GraphWorX's own rendering."""
        ref = self.d.get("_ref_abs")
        if not ref or not os.path.exists(ref):
            return out
        try:
            from PIL import Image
            im = Image.open(ref).convert("RGB")
        except Exception:
            return out
        from ..web_geometry import bbox_of
        boxes = [bbox_of(o) for o in out]
        moved = 0
        for _ in range(3):
            changed = False
            for ai in range(len(out)):
                a = out[ai]
                if a.get("type") not in ("rect", "ellipse", "polygon", "group") or (a.get("fill") in (None, "none") and a.get("type") != "group"):
                    continue
                ax, ay, aw, ah = boxes[ai]
                if aw * ah < 900:
                    continue
                victims = []
                for bi in range(ai):
                    bx, by, bw, bh = boxes[bi]
                    if bw * bh * 3 > aw * ah or not (ax - 1 <= bx and ay - 1 <= by and bx + bw <= ax + aw + 1 and by + bh <= ay + ah + 1):
                        continue
                    if self._visible_in_ref(im, out[bi], boxes[bi], a):
                        victims.append(bi)
                if victims:
                    first = min(victims)
                    obj, box = out.pop(ai), boxes.pop(ai)
                    out.insert(first, obj)
                    boxes.insert(first, box)
                    moved += 1
                    changed = True
            if not changed:
                break
        if moved:
            self.func["restacked"] = moved
        return out

    @staticmethod
    def _visible_in_ref(im, b, box, a):
        """does object b show in the reference where a (above it) would cover it?"""
        x, y, w, h = box
        fill = a.get("fill") or ""
        if not fill.startswith("#") or len(fill) != 7:
            return True if b.get("type") in ("text", "value", "input", "image", "line", "polyline") else False
        fr, fg, fb = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
        diff = tot = 0
        for iy in range(1, 6):
            for ix in range(1, 6):
                px, py = int(x + w * ix / 6), int(y + h * iy / 6)
                if 0 <= px < im.width and 0 <= py < im.height:
                    r, g, bl = im.getpixel((px, py))
                    tot += 1
                    if abs(r - fr) + abs(g - fg) + abs(bl - fb) > 60:
                        diff += 1
        return tot > 0 and diff / tot > 0.3

    # ------------------------------------------------------------------ isolated renders from the exporter
    def raster_file(self, o):
        """copy the object's isolated render into the project -> (images/... , [x, y, w, h])"""
        f = o.get("_raster")
        if not f or not self.d.get("_work"):
            return None
        src = os.path.join(self.d["_work"], f)
        if not os.path.exists(src):
            return None
        rel = f"images/{self.d['_name']}_{os.path.splitext(f)[0].rsplit('_', 1)[-1]}.png"
        shutil.copy(src, os.path.join(self.d["_proj_root"], rel))
        return rel, [float(v) for v in o.get("_rbox", o.get("_dims"))]

    def raster_texture(self, o):
        r = self.raster_file(o)
        if not r:
            return None
        self.func["textures"] = self.func.get("textures", 0) + 1
        return r[0], r[1]

    def vector(self, o):
        f = o.get("_raster")
        if not f or not self.d.get("_work") or o.get("_raster_kind") != "shape":
            return None
        try:
            from .vectorize import vectorize
            rb = o.get("_rbox") or o.get("_dims")
            return vectorize(os.path.join(self.d["_work"], f), bool(o.get("isFilled")), float(rb[0]), float(rb[1]), eps=1.5)
        except Exception as e:
            self.warn.append(f"vectorize failed: {e}")
            return None

    def texture(self, r):
        """GraphWorX pattern/bitmap brushes are not exposed by automation: if the shape's interior
        in the GraphWorX rendering is textured, crop it and use it as an SVG pattern fill."""
        ref = self.d.get("_ref_abs")
        if not ref or not os.path.exists(ref):
            return None
        try:
            from PIL import Image
        except ImportError:
            return None
        if not hasattr(self, "_ref_img"):
            self._ref_img = Image.open(ref).convert("RGB")
        im = self._ref_img
        if r["type"] == "polygon":
            xs, ys = [p[0] for p in r["points"]], [p[1] for p in r["points"]]
            bx, by, bw, bh = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
            inside = lambda x, y: _in_poly(x, y, r["points"])
        else:
            bx, by, bw, bh = r["x"], r["y"], r["w"], r["h"]
            if r["type"] == "ellipse":
                cx, cy = bx + bw / 2, by + bh / 2
                inside = lambda x, y: ((x - cx) / (bw / 2 or 1)) ** 2 + ((y - cy) / (bh / 2 or 1)) ** 2 <= 0.8
            else:
                inside = lambda x, y: bx + 3 < x < bx + bw - 3 and by + 3 < y < by + bh - 3
        if bw < 12 or bh < 12:
            return None
        lum = []
        for iy in range(1, 24):
            for ix in range(1, 24):
                x, y = bx + bw * ix / 24, by + bh * iy / 24
                if inside(x, y) and 0 <= x < im.width and 0 <= y < im.height:
                    p = im.getpixel((int(x), int(y)))
                    lum.append(0.3 * p[0] + 0.59 * p[1] + 0.11 * p[2])
        if len(lum) < 20:
            return None
        mean = sum(lum) / len(lum)
        sd = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
        if sd < 10:
            return None                                   # uniform fill -> plain color is exact
        bins = {}
        for v in lum:
            bins[int(v) // 12] = bins.get(int(v) // 12, 0) + 1
        if max(bins.values()) / len(lum) > 0.7:
            return None                                   # one dominant shade + some overlapping text/objects: not a pattern
        rel = f"images/{self.d['_name']}_tex{self.n}_{int(bx)}_{int(by)}.png"
        im.crop((int(bx), int(by), int(bx + bw + 1), int(by + bh + 1))).save(os.path.join(self.d["_proj_root"], rel))
        return rel

    def crop(self, x, y, w, h):
        src = self.d.get("_ref_abs") or self.d.get("_bg_abs")
        if not src or not os.path.exists(src):
            return None
        try:
            from PIL import Image
        except ImportError:
            return None
        im = Image.open(src)
        box = (max(0, int(x)), max(0, int(y)), min(im.width, int(x + w + 1)), min(im.height, int(y + h + 1)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        rel = f"images/{self.d['_name']}_{self.n}.png"
        im.crop(box).save(os.path.join(self.d["_proj_root"], rel))
        return rel

    # ------------------------------------------------------------------ dynamics
    def dynamics(self, o, r):
        for dy in o.get("_dyn", []):
            t = (dy.get("_type") or "").replace("Gwx", "")
            src = dy.get("dataSource") or ""
            ex = self.map.expr(src)
            lo, hi = _num(dy.get("lowRange"), 0), _num(dy.get("highRange"), 100)
            if src:
                self.func["bindings"] += 1
            dyn = r.setdefault("dyn", {})
            if t == "ProcessPoint":
                entry = bool(dy.get("UseKeypad") or dy.get("DataEntry"))
                if r["type"] in ("text", "button"):
                    r["type"] = "input" if entry else "value"
                    r["expr" if not entry else "tag"] = ex if not entry else self.map.ref(src)
                    r["fmt"] = _fmt(dy.get("Format"))
                    r["placeholder"] = r.pop("text", "")     # GraphWorX design-time text (e.g. "???")
                    if dy.get("ConfirmBeforeWrite"):
                        r["confirm"] = True
                else:
                    dyn["text"] = {"expr": ex, "fmt": _fmt(dy.get("Format"))}
                self.note("converted", src, "process point" + (" (data entry)" if entry else ""))
            elif t == "Hide":
                hide_when_true = dy.get("HideWhenTrue", True) is not False
                dyn["visible"] = {"expr": ex, "invert": hide_when_true}
                self.note("converted", src, "visibility")
            elif t == "Flash":
                dyn["blink"] = {"expr": ex if dy.get("FlashWhenDataSourceTrue", True) else f"!({ex})"}
                self.note("converted", src, "blink")
            elif t == "AnalogColor":
                c0 = color(dy.get("StartFillColor"), None) if dy.get("StartFillColor") is not None else "#0000ff"
                c1 = color(dy.get("EndFillColor"), None) if dy.get("EndFillColor") is not None else "#ff0000"
                dyn["fill"] = {"expr": ex, "type": "blend", "min": lo, "max": hi, "from": c0, "to": c1}
                self.note("converted" if dy.get("StartFillColor") is not None else "approximated", src, "analog color")
            elif t == "DigitalColor":
                subs = [s for s in dy.get("_subs", []) if s.get("dataSource")]
                if subs:
                    m, chain = {"0": r.get("fill", "none")}, "0"
                    for k, s in reversed(list(enumerate(subs, 1))):
                        chain = f"iif({self.map.expr(s['dataSource'])}, {k}, {chain})"
                        m[str(k)] = color(s.get("fillColor"))
                        self.func["bindings"] += 1
                        self.note("converted", s["dataSource"], "digital color")
                    key = "stroke" if r["type"] in ("line", "polyline") or all(s.get("UseLineColor") and not s.get("UseFillColor") for s in subs) else "fill"
                    if key == "stroke":
                        m = {"0": r.get("stroke", "#000000"), **{k: color(s.get("lineColor")) for k, s in zip(map(str, range(1, len(subs) + 1)), subs)}}
                    dyn[key] = {"expr": chain, "type": "map", "map": m}
                elif src:
                    if r["type"] in ("line", "polyline"):
                        dyn["stroke"] = {"expr": ex, "type": "bool", "on": color(dy.get("lineColor"), "#ff0000"), "off": r.get("stroke", "#000000")}
                    else:
                        dyn["fill"] = {"expr": ex, "type": "bool", "on": color(dy.get("fillColor"), "#00ff00"), "off": r.get("fill", "none")}
                    self.note("approximated", src, "digital color")
            elif t == "Location":
                ox, oy = _num(dy.get("OffsetX"), 0), _num(dy.get("OffsetY"), 0)
                if ox or oy:
                    if ox:
                        dyn["x"] = {"expr": ex, "in": [lo, hi], "out": [0, ox]}
                    if oy:
                        dyn["y"] = {"expr": ex, "in": [lo, hi], "out": [0, oy]}
                    self.note("converted", src, "location")
                else:
                    dyn["x"] = {"expr": ex, "in": [lo, hi], "out": [0, 0]}
                    self.note("approximated", src, "location (offset not exposed by this GraphWorX version)")
            elif t == "Rotation":
                dyn["rot"] = {"expr": ex, "in": [lo, hi], "out": [-_num(dy.get("StartAngle"), 0), -_num(dy.get("EndAngle"), 360)]}
                self.note("converted", src, "rotation")
            elif t == "Size":
                dyn["scale"] = {"expr": ex, "in": [lo, hi], "out": [_num(dy.get("StartSize"), 0) / 100 or 0, _num(dy.get("EndSize"), 100) / 100 or 1], "anchor": "bottom", "dir": "v"}
                self.note("converted", src, "size")
            elif t == "Timedate":
                if r["type"] in ("text", "button"):
                    r["type"] = "value"
                    r["expr"] = "{{sys:DateTime}}"
                    r["placeholder"] = r.pop("text", "")
                self.note("converted", "sys:DateTime", "time/date")
            elif t == "Pick":
                acts = self.pick(dy, src)
                if acts:
                    r.setdefault("actions", []).extend(acts)
            elif t == "Animator" and r["type"] == "group":
                dyn["animate"] = {"expr": ex or "1", "ms": int(dy.get("TimerRate") or 500), "offFrame": 0}
                self.note("converted", src or "always", "animator")
            elif t in ("DigitalSelector", "AnalogSelector", "Animator"):
                subs = dy.get("_subs", [])
                if r["type"] == "group" and subs:
                    for k, s in enumerate(subs):
                        if k < len(r["children"]) and s.get("dataSource"):
                            r["children"][k].setdefault("dyn", {})["visible"] = {"expr": self.map.expr(s["dataSource"])}
                            self.func["bindings"] += 1
                            self.note("converted", s["dataSource"], f"{t} frame {k}")
                elif src:
                    self.note("unsupported", src, t)
                    self.warn.append(f"{t} on {r.get('id')} not converted")
            else:
                if src:
                    self.note("unsupported", src, t)
                self.warn.append(f"dynamic {t} on {r.get('id')} not converted")
            if not dyn:
                r.pop("dyn", None)

    def pick(self, dy, src):
        a = dy.get("pickAction")
        name = PICK[a] if isinstance(a, int) and 0 <= a < len(PICK) else str(a)
        param = dy.get("NameParameter") or ""
        disp = lambda p: f"{self.folder}/{os.path.splitext(os.path.basename(p))[0]}.nll" if p else ""
        aliases = _aliases(dy.get("aliasCommandString"))
        if name == "LoadDisplay" or (a is None and param.lower().endswith(".gdf")):
            self.note("converted", param, "open display")
            return [{"on": "click", "type": "open", "display": disp(param), "target": "main", **({"aliases": aliases} if aliases else {})}]
        if name == "PopupWindow":
            self.note("converted", param, "popup")
            return [{"on": "click", "type": "open", "display": disp(param), "target": "popup", **({"aliases": aliases} if aliases else {})}]
        if name == "DownloadValue":
            self.note("converted", src, "write value")
            return [{"on": "click", "type": "write", "tag": self.map.ref(src), "value": str(dy.get("Value1") or 1)}]
        if name == "ToggleValue":
            self.note("converted", src, "toggle")
            return [{"on": "click", "type": "toggle", "tag": self.map.ref(src)}]
        if name == "Close":
            return [{"on": "click", "type": "close"}]
        if name == "DisplayBack":
            return [{"on": "click", "type": "back"}]
        if name == "DisplayForward":
            return [{"on": "click", "type": "forward"}]
        if name == "SetAliases" and aliases:
            return [{"on": "click", "type": "aliasSet", "set": "|".join(f"{k}={v}" for k, v in aliases.items())}]
        if name == "LaunchWebPage" and param:
            return [{"on": "click", "type": "url", "href": param}]
        if name == "PopupUserMenu":
            subs = [s for s in dy.get("_subs", []) if s.get("NameParameter")]
            out = []
            for s in subs[:1]:
                out += self.pick(s, s.get("dataSource") or "")
            if not out:
                self.note("approximated", param or name, "popup menu (first item kept)")
            return out
        if name == "RunScript":
            self.note("approximated", param, "VBA script -> stub (port to scripts/*.js)")
            self.warn.append(f"pick runs VBA '{param}' - port it to a display script")
            return [{"on": "click", "type": "script", "fn": re.sub(r"\W", "_", param.split(".")[-1] or "vba_script")}]
        self.note("unsupported", param or name, f"pick action {name}")
        return []


def _in_poly(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def _num(v, d):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return d


def _fmt(f):
    if not f:
        return "0.00"
    m = re.search(r"\.(\d+)", str(f))
    if "%" in str(f) and m:
        return "0." + "0" * int(m.group(1)) if int(m.group(1)) else "0"
    return str(f)


def _aliases(cmd):
    out = {}
    for part in re.split(r"[;,]", cmd or ""):
        m = re.match(r"\s*<#?([^#=<>]+)#?>?\s*=\s*(.*)", part)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


# --------------------------------------------------------------------------------------- entry
def export_gdf(files: list[str], out_dir: str, snapshots=True, bake=True, timeout=900) -> list[dict]:
    if sys.platform != "win32":
        raise RuntimeError("GraphWorX import needs Windows with GENESIS32 installed")
    args = [powershell32(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
            os.path.join(HERE, "gwx_export.ps1"), "-Files", "|".join(files), "-Out", out_dir]
    if snapshots:
        args.append("-Snapshots")
    if bake:
        args.append("-Bake")
    p = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=0x08000000)
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        raise RuntimeError("GraphWorX export failed: " + (p.stderr.decode("cp866", "replace") or out)[-1500:])


def import_gwx(project: Project, params: dict) -> dict:
    raw = [f.strip().strip('"') for f in re.split(r"[;\n]", params.get("files", "")) if f.strip()]
    files = []
    for f in raw:
        if os.path.isdir(f):
            files += [os.path.join(f, x) for x in sorted(os.listdir(f)) if x.lower().endswith(".gdf")]
        elif os.path.isfile(f):
            files.append(f)
    if not files:
        raise FileNotFoundError("no .gdf files found")
    folder = (params.get("folder") or "displays/imported").strip("/")
    mode = params.get("mode") or "native"
    snapshots = params.get("snapshots", True) not in (False, "false", "0")
    mapper = TagMapper(params.get("tagmap", ""))
    work = tempfile.mkdtemp(prefix="gf-gwx-")
    os.makedirs(os.path.join(project.root, "images"), exist_ok=True)
    os.makedirs(project.abspath(folder), exist_ok=True)
    summary = export_gdf(files, work, snapshots=True, bake=(mode == "hybrid"))
    created, warnings, identity = [], [], {}
    for rep in summary:
        if not rep.get("ok"):
            warnings.append(f"{rep.get('file')}: {rep.get('error')}")
            continue
        dump = json.load(open(os.path.join(work, rep["json"]), encoding="utf-8"))
        name = os.path.splitext(rep["json"])[0]
        safe = re.sub(r"[^\w\-]+", "_", name)
        dump["_name"], dump["_proj_root"], dump["_work"] = safe, project.root, work
        bg_rel = None
        if dump.get("bg"):
            bg_rel = f"images/{safe}_bg.png"
            shutil.copy(os.path.join(work, dump["bg"]), os.path.join(project.root, bg_rel))
            dump["_bg_abs"] = os.path.join(project.root, bg_rel)
        if dump.get("ref"):
            refdir = os.path.join(project.root, "runtime", "identity")
            os.makedirs(refdir, exist_ok=True)
            shutil.copy(os.path.join(work, dump["ref"]), os.path.join(refdir, f"{safe}.ref.png"))
            dump["_ref_abs"] = os.path.join(refdir, f"{safe}.ref.png")
        conv = Converter(dump, mapper, mode, bg_rel, folder)
        doc = conv.convert_display(name)
        rel = f"{folder}/{safe}.nll"
        project.write_text(rel, json.dumps(doc, ensure_ascii=False, indent=2) + "\n", backup=True)
        created.append(rel)
        warnings += [f"{safe}: {w}" for w in conv.warn]
        warnings += [f"{safe}: GraphWorX dialog auto-answered: {d}" for d in dump.get("dialogs", []) if "Display1" not in d]
        f = conv.func
        identity[safe] = {"functional": {k: f[k] for k in ("bindings", "converted", "approximated", "unsupported")},
                          "objects": len(dump.get("objects", [])), "enumeration": rep.get("enumeration")}
        with open(os.path.join(project.root, "runtime", "identity", f"{safe}.functional.json"), "w", encoding="utf-8") as fh:
            json.dump(f, fh, ensure_ascii=False, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    if snapshots and created:
        try:
            from .visual import compare_displays
            vis = compare_displays(project, created)
            for k, v in vis.items():
                identity.setdefault(k, {})["visual"] = v
        except Exception as e:
            warnings.append(f"visual identity test skipped: {e}")
    return {"summary": f"GraphWorX: {len(created)} of {len(files)} display(s) imported ({mode})", "files": created,
            "warnings": warnings[:500], "identity": identity}
