"""Python twin of the bounding box logic in web/js/objects.js (used by importers)."""
from __future__ import annotations


def bbox_of(o: dict):
    pts = o.get("points")
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(1, max(xs) - min(xs)), max(1, max(ys) - min(ys))
    if o.get("type") == "group":
        kids = [bbox_of(c) for c in o.get("children", [])]
        if not kids:
            return o.get("x", 0), o.get("y", 0), 1, 1
        x0 = min(k[0] for k in kids)
        y0 = min(k[1] for k in kids)
        x1 = max(k[0] + k[2] for k in kids)
        y1 = max(k[1] + k[3] for k in kids)
        return x0, y0, x1 - x0, y1 - y0
    return o.get("x", 0), o.get("y", 0), o.get("w", 0), o.get("h", 0)
